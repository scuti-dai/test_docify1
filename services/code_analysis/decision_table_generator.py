"""
Decision Table Generator Module

This module provides functions to convert JSON decision_dict to markdown:
- generate_func_decision_table: Generate decision table in markdown format
- generate_func_test_patterns: Generate test patterns table in markdown format
- generate_decision_table_and_test_pattern: Generate both decision_table and test_pattern from unit_test_design_json
"""

import json
import logging
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)


def generate_func_decision_table(
    decision_dict: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """
    Generate decision table in markdown format from decision_dict

    Args:
        decision_dict: Dictionary containing test cases with format:
            {
                "1": {
                    "input": {"key1": value1, ...},
                    "output": {"key1": value1, ...},
                    "note": "note text"
                },
                "2": {...},
                ...
            }

    Returns:
        Tuple[str, Dict]:
            - decision_str: Markdown string of decision table
            - all_dict: Dictionary containing all collected input/output values
    """
    try:
        lines = ["デシジョンテーブル\n\n----\n"]
        all_dict = {"input": {}, "output": {}}

        # Collect all input/output from test cases
        for values in decision_dict.values():
            all_dict["input"] = _get_all_dict_sub(all_dict["input"], values["input"])
            all_dict["output"] = _get_all_dict_sub(all_dict["output"], values["output"])

        # OPTIMIZATION - Use list.append() and join instead of string concatenation
        # O(n) complexity instead of O(n²)
        # Create table header
        lines.append("|I/O|項目|値|" + "|".join(decision_dict.keys()) + "|")
        lines.append("|---|----|--|" + "-|" * len(decision_dict))

        # Create Input section
        input_decision_dict = {k: v["input"] for k, v in decision_dict.items()}
        lines.append(_get_decision_part(all_dict["input"], input_decision_dict, "I"))

        # Create Output section
        output_decision_dict = {k: v["output"] for k, v in decision_dict.items()}
        lines.append(_get_decision_part(all_dict["output"], output_decision_dict, "O"))

        decision_str = "\n".join(lines)
        return decision_str, all_dict
    except Exception as e:
        logger.error(f"[generate_func_decision_table] Error: {e}")
        raise


def generate_func_test_patterns(
    decision_dict: Dict[str, Any], all_dict: Dict[str, Any]
) -> str:
    """
    Generate test patterns table in markdown format from decision_dict and all_dict

    Args:
        decision_dict: Dictionary containing test cases (same as input of generate_func_decision_table)
        all_dict: Dictionary from the result of generate_func_decision_table

    Returns:
        str: Markdown string of test patterns table
    """
    try:
        lines = ["テストパターン\n\n----\n", "|テストID"]
        all_input = all_dict["input"].keys()
        all_output = []

        # Get list of output keys (excluding exception)
        for k in all_dict["output"].keys():
            if k != "exception":
                all_output.append(k)

        # Add input columns to header
        for item in all_input:
            if item == "database":
                lines.append("|データベースからの入力")
            elif item == "method":
                lines.append("|メソッド実行結果")
            else:
                lines.append(f"|{item}")

        # Add output columns to header
        for item in all_output:
            if item == "database":
                lines.append("|データベースへの出力")
            elif item == "exception":
                pass
            else:
                lines.append("|期待される結果")

        lines.append("|備考|")
        pattern_str = "".join(lines) + "\n"
        pattern_str += "|-" * (len(all_input) + len(all_output) + 2) + "|\n"

        # Create test case rows
        for n, d in decision_dict.items():
            row_parts = [f"|TC{n}"]
            input_dict = d["input"]

            # Add input values
            for k in all_input:
                row_parts.append(f"|{input_dict[k]}" if k in input_dict else "|(なし)")

            # Add output values
            output_dict = d["output"]
            for k in all_output:
                if k == "return_values":
                    return_values = output_dict[k] if k in output_dict else None
                    # If exception exists, display exception, otherwise display return_values
                    if (
                        "exception" in output_dict
                        and output_dict["exception"] is not None
                    ):
                        row_parts.append(f"|{output_dict['exception']}")
                    else:
                        row_parts.append(f"|{return_values}")
                else:
                    row_parts.append(
                        f"|{output_dict[k]}" if k in output_dict else "|(なし)"
                    )

            # Add note
            row_parts.append(f"|{d['note']}|")
            pattern_str += "".join(row_parts) + "\n"

        return pattern_str
    except Exception as e:
        logger.error(f"[generate_func_test_patterns] Error: {e}")
        raise


async def generate_decision_table_and_test_pattern(
    unit_test_design_json: Any,
    project_id: str,
    unit_test_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Generate decision_table and test_pattern from unit_test_design_json

    Args:
        unit_test_design_json: JSON string or dict containing decision table data
        unit_test_id: Optional unit test ID to update in database

    Returns:
        Tuple[Optional[str], Optional[str]]: (decision_table, test_pattern) or (None, None) on error
    """
    if not unit_test_design_json:
        logger.warning(
            "[generate_decision_table_and_test_pattern] Missing unit_test_design_json"
        )
        return None, None

    try:
        logger.info("[generate_decision_table_and_test_pattern] Start")

        # Parse JSON if it's a string
        if isinstance(unit_test_design_json, str):
            decision_dict = json.loads(unit_test_design_json)
        else:
            decision_dict = unit_test_design_json

        if not isinstance(decision_dict, dict):
            logger.warning(
                "[generate_decision_table_and_test_pattern] Invalid decision_dict format"
            )
            return None, None

        # Generate decision table and get all_dict
        decision_table, all_dict = generate_func_decision_table(decision_dict)

        # Generate test patterns
        test_pattern = generate_func_test_patterns(decision_dict, all_dict)

        logger.info("[generate_decision_table_and_test_pattern] Success")

        # Update unit test if ID is provided
        if decision_table and test_pattern and unit_test_id:
            # Import here to avoid circular import
            from app.services.unit_test_service import unit_test_service

            decision_table = decision_table.replace("|\n\n|", "|\n|")
            await unit_test_service.update_unit_test(
                unit_test_id, project_id, decision_table, test_pattern
            )

        return decision_table, test_pattern
    except Exception as e:
        error_message = "[generate_decision_table_and_test_pattern] Error: %s" % (e,)
        logger.error(error_message)
        return None, None


def _get_decision_part(
    all_dict: Dict[str, Any],
    decision_dict: Dict[str, Any],
    i_o_str: str,
    add_str: str = "",
) -> str:
    """
    Helper function: Create rows for Input or Output section in decision table

    Args:
        all_dict: Dictionary containing all input/output values
        decision_dict: Dictionary containing input/output of each test case
        i_o_str: "I" for input or "O" for output
        add_str: Prefix string (e.g., "database: ", "method: ")

    Returns:
        str: Markdown string of decision table rows
    """
    try:
        out_str = ""
        for k, v in all_dict.items():
            # Handle recursive processing for database and method (nested dict)
            if k == "database" or k == "method":
                nested_decision_dict = {
                    n: x[k] if k in x else {} for n, x in decision_dict.items()
                }
                out_str += _get_decision_part(
                    v, nested_decision_dict, i_o_str, k + ": "
                )
                continue

            # Create rows for each value
            for vv in v:
                out_str += f"|{i_o_str}|{add_str}{k}|{vv}|"
                # Mark ○ if test case has this value
                for x in decision_dict.values():
                    if k is not None and k in x and vv == x[k]:
                        out_str += "○|"
                    else:
                        out_str += " |"
                out_str += "\n"
        return out_str
    except Exception as e:
        logger.error(f"[_get_decision_part] Error: {e}")
        raise


def _get_all_dict_sub(
    all_dict: Dict[str, Any], input_dict: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Helper function: Collect all input/output values from test cases into all_dict

    Args:
        all_dict: Dictionary to aggregate values
        input_dict: Input/output dictionary from one test case

    Returns:
        Dict: Updated all_dict
    """
    try:
        if not isinstance(input_dict, dict):
            logger.error(
                f"[_get_all_dict_sub] Error: Expected input_dict to be a dictionary, but got {type(input_dict)}"
            )
            return all_dict

        for k in input_dict:
            # Handle recursive processing for database and method (nested dict)
            if k == "database" or k == "method":
                if k not in all_dict:
                    all_dict[k] = {}
                if input_dict[k] is not None:
                    all_dict[k] = _get_all_dict_sub(all_dict[k], input_dict[k])
            else:
                v = input_dict[k]
                # Convert dict/list to JSON string
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                    input_dict[k] = v
                # Handle empty string
                if v == "":
                    v = "(空文字)"
                    input_dict[k] = v
                # Add to list if not exists
                if k not in all_dict:
                    all_dict[k] = []
                if v not in all_dict[k]:
                    all_dict[k].append(v)
        return all_dict
    except Exception as e:
        logger.error(f"[_get_all_dict_sub] Error: {e}")
        raise


# Example usage
if __name__ == "__main__":
    # Usage example
    example_decision_dict = {
        "1": {
            "input": {
                "coefficient": "abc",
                "flg": 2,
                "database": {"logic": True},
                "method": {"m_1()": "TypeError"},
            },
            "output": {"return_values": None, "exception": "TypeError"},
            "note": "入力が型違いのケース",
        },
        "2": {
            "input": {
                "coefficient": 6,
                "flg": 1,
                "database": {"logic": False},
                "method": {"m_1()": "ValueError"},
            },
            "output": {"return_values": None, "exception": "ValueError"},
            "note": "入力が範囲外のケース",
        },
        "3": {
            "input": {
                "coefficient": 5,
                "flg": 2,
                "database": {"logic": True},
                "method": {"m_1()": 5},
            },
            "output": {
                "return_values": 10,
                "database": {"result": 10},
                "exception": None,
            },
            "note": "正常系",
        },
    }
