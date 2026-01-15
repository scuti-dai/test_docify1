from __future__ import annotations
from sentan_tools.code import (
    JavaFile,
    parse_code_file,
    ProgrammingCode,
    CodeFunction,
    CodeClass,
    AccessModifier,
)
from app.utils.constants import LanguageType
from antlr4 import *
from antlr_parser.JavaLexer import JavaLexer
from antlr_parser.JavaParser import JavaParser
from antlr_parser.JavaParserListener import JavaParserListener
from antlr_parser.CSharpLexer import CSharpLexer
from antlr_parser.CSharpParser import CSharpParser
from antlr_parser.CSharpParserListener import CSharpParserListener
import logging

logger = logging.getLogger(__name__)


def function_info_to_dict(func: CodeFunction):
    return {"name": func.name, "code": func.get_code()}


class CodeAnalyzer:
    # class MyJavaFile:
    def __init__(self, code_string, file_path=None, language: str = None):
        self._language = language
        self._code_tree = None
        self._file_path = file_path
        if self._language == "Java":
            self._code = code_string
            self._code_tree = self._parse_java_code()

        elif self._language == "Csharp":
            code_string = code_string.replace('$@"', '"$@').replace('$"', '"$')
            self._code = code_string
            self._code_tree = self._parse_csharp_code()

    @property
    def file_path(self):
        return self._file_path

    def __getstate__(self):
        # Copy the object's state and remove '_code_tree' for pickling
        state = self.__dict__.copy()
        if "_code_tree" in state:
            del state["_code_tree"]  # Remove non-pickleable field
        return state

    def __setstate__(self, state):
        # Restore object's state and recreate '_code_tree' after unpickling
        self.__dict__.update(state)
        if self._language == "Java":
            self._code_tree = self._parse_java_code()
        elif self._language == "Csharp":
            self._code_tree = self._parse_csharp_code()

    def get_code(self):
        return self._code

    def get_language(self):
        return self._language

    def _parse_java_code(self):
        input_stream = InputStream(self._code)
        lexer = JavaLexer(input_stream)
        stream = CommonTokenStream(lexer)
        parser = JavaParser(stream)
        tree = parser.compilationUnit()
        return tree

    def _parse_csharp_code(self):
        input_stream = InputStream(self._code)
        lexer = CSharpLexer(input_stream)
        stream = CommonTokenStream(lexer)
        parser = CSharpParser(stream)
        tree = parser.compilation_unit()
        return tree

    def get_top_level_nodes(self):
        top_level_nodes = {"classes": [], "methods": []}
        if self._language == "Java" and self._code_tree:
            listener = JavaClassMethodListener(top_level_nodes, self._code)
            walker = ParseTreeWalker()
            walker.walk(listener, self._code_tree)

        elif self._language == "Csharp" and self._code_tree:
            listener = CSharpClassMethodListener(top_level_nodes, self._code)
            walker = ParseTreeWalker()
            walker.walk(listener, self._code_tree)
        return top_level_nodes


class JavaClassMethodListener(JavaParserListener):
    def __init__(self, top_level_nodes, code):
        super().__init__()
        self.top_level_nodes = top_level_nodes
        self.code = code

    def enterClassDeclaration(self, ctx):
        logger.debug(f"[enterClassDeclaration] Processing class declaration - ctx={ctx}")
        logger.debug(f"[enterClassDeclaration] Listener instance={self}")
        class_name = ctx.getChild(1).getText()
        start_index = ctx.start.start
        end_index = ctx.stop.stop
        class_code = self.code[start_index : end_index + 1]

        # Create a dictionary to store class information
        class_info = {"name": class_name, "code": class_code, "methods": []}

        for child in ctx.children:
            if isinstance(child, JavaParser.ClassBodyContext):
                for body_child in child.children:
                    if isinstance(body_child, JavaParser.ClassBodyDeclarationContext):
                        for member_ctx in body_child.children:
                            if isinstance(
                                member_ctx, JavaParser.MemberDeclarationContext
                            ):
                                for method_ctx in member_ctx.children:
                                    if isinstance(
                                        method_ctx, JavaParser.MethodDeclarationContext
                                    ):
                                        method_name = method_ctx.getChild(1).getText()
                                        method_start_index = method_ctx.start.start
                                        method_end_index = method_ctx.stop.stop
                                        method_code = self.code[
                                            method_start_index : method_end_index + 1
                                        ]

                                        class_info["methods"].append(
                                            {"name": method_name, "code": method_code}
                                        )
        # Add class information to the top-level nodes list
        self.top_level_nodes["classes"].append(class_info)

    def enterMethodDeclaration(self, ctx):
        method_name = ctx.getChild(1).getText()
        # method_name = ctx.IDENTIFIER().getText()
        start_index = ctx.start.start
        end_index = ctx.stop.stop
        method_code = self.code[start_index : end_index + 1]
        self.top_level_nodes["methods"].append(
            {"name": method_name, "code": method_code}
        )


class CSharpClassMethodListener(CSharpParserListener):
    def __init__(self, top_level_nodes, code):
        super().__init__()
        self.top_level_nodes = top_level_nodes
        self.code = code

    def enterClass_definition(self, ctx):
        # Get the class name and code
        class_name = ctx.identifier().getText()
        start_index = ctx.start.start
        end_index = ctx.stop.stop
        class_code = self.code[start_index : end_index + 1]

        # Create dictionary to store class information
        class_info = {"name": class_name, "code": class_code, "methods": []}

        # Iterate through the class members to find methods
        for child in ctx.children:
            if isinstance(child, CSharpParser.Class_bodyContext):
                for body_child in child.children or []:
                    if isinstance(
                        body_child, CSharpParser.Class_member_declarationsContext
                    ):
                        for member_ctx in body_child.children:
                            if isinstance(
                                member_ctx, CSharpParser.Class_member_declarationContext
                            ):
                                for common_member in member_ctx.children:
                                    if isinstance(
                                        common_member,
                                        CSharpParser.Common_member_declarationContext,
                                    ):
                                        if common_member and common_member.children:
                                            # Check for Method_declarationContext directly in Common_member_declarationContext
                                            for method_ctx in common_member.children:
                                                if isinstance(
                                                    method_ctx,
                                                    CSharpParser.Method_declarationContext,
                                                ):
                                                    # Process method_declaration
                                                    method_name = (
                                                        method_ctx.method_member_name().getText()
                                                    )
                                                    method_start_index = (
                                                        method_ctx.start.start
                                                    )
                                                    method_end_index = (
                                                        method_ctx.stop.stop
                                                    )
                                                    method_code = self.code[
                                                        method_start_index : method_end_index
                                                        + 1
                                                    ]
                                                    logger.debug(
                                                        f"[enterClass_definition] Found method in Common_member_declarationContext: {method_name}"
                                                    )

                                                    # Save method information to class_info
                                                    class_info["methods"].append(
                                                        {
                                                            "name": method_name,
                                                            "code": method_code,
                                                        }
                                                    )
                                            # Check for method_declaration in typed_member_declaration (inside Common_member_declarationContext)
                                            for typed_member in common_member.children:
                                                if isinstance(
                                                    typed_member,
                                                    CSharpParser.Typed_member_declarationContext,
                                                ):
                                                    for (
                                                        method_ctx
                                                    ) in typed_member.children:
                                                        if isinstance(
                                                            method_ctx,
                                                            CSharpParser.Method_declarationContext,
                                                        ):
                                                            # Process method_declaration
                                                            method_name = (
                                                                method_ctx.method_member_name().getText()
                                                            )
                                                            method_start_index = (
                                                                method_ctx.start.start
                                                            )
                                                            method_end_index = (
                                                                method_ctx.stop.stop
                                                            )
                                                            method_code = self.code[
                                                                method_start_index : method_end_index
                                                                + 1
                                                            ]
                                                            logger.debug(
                                                                f"[enterClass_definition] Found method in typed_member_declaration: {method_name}"
                                                            )

                                                            # Save method information to class_info
                                                            class_info[
                                                                "methods"
                                                            ].append(
                                                                {
                                                                    "name": method_name,
                                                                    "code": method_code,
                                                                }
                                                            )
                                                else:
                                                    logger.warning(
                                                        "[enterClass_definition] typed_member_declaration not found"
                                                    )
                                        else:
                                            logger.warning(
                                                "[enterClass_definition] common_member is None or has no children"
                                            )
        # Add class information to the top-level nodes list
        self.top_level_nodes["classes"].append(class_info)

    def enterMethod_declaration(self, ctx):
        method_name = ctx.method_member_name().getText()
        start_index = ctx.start.start
        end_index = ctx.stop.stop
        logger.debug(f"[enterMethod_declaration] Found method: {method_name}")
        method_code = self.code[start_index : end_index + 1]
        self.top_level_nodes["methods"].append(
            {"name": method_name, "code": method_code}
        )

    def enterGlobal_method_declaration(self, ctx):
        method_name = None

        try:
            # 複数の方法でメソッド名取得を試行
            if ctx.method_member_name() is not None:
                method_name = ctx.method_member_name().getText()
            elif hasattr(ctx, "identifier") and ctx.identifier() is not None:
                method_name = ctx.identifier().getText()
            elif hasattr(ctx, "IDENTIFIER") and ctx.IDENTIFIER() is not None:
                method_name = ctx.IDENTIFIER().getText()
            else:
                # コンテキストから直接テキストを取得
                method_name = f"method_at_line_{ctx.start.line}"

        except (AttributeError, TypeError) as e:
            logger.error(f"[enterGlobal_method_declaration] Error extracting method name: {e}")
            method_name = f"unknown_method_{ctx.start.start}"

        # メソッド名が取得できた場合のみ処理を続行
        if method_name:
            start_index = ctx.start.start
            end_index = ctx.stop.stop
            logger.debug(f"[enterGlobal_method_declaration] Found global method: {method_name}")
            method_code = self.code[start_index : end_index + 1]
            self.top_level_nodes["methods"].append(
                {"name": method_name, "code": method_code}
            )
        else:
            logger.warning(
                "[enterGlobal_method_declaration] Skipping method declaration due to name extraction failure"
            )


def get_code_file(file_path=None, code_string=None):
    if file_path.endswith(".java"):
        if code_string:
            java_file = CodeAnalyzer(code_string, file_path, language="Java")
        else:
            java_file = CodeAnalyzer(file_path)

        return java_file
    elif file_path.endswith(".py"):
        code = ProgrammingCode(code_string.split("\n"), file_path)
        return parse_code_file(file_path, code=code)
    else:
        if code_string:
            csharp_file = CodeAnalyzer(code_string, file_path, language="Csharp")
        else:
            csharp_file = CodeAnalyzer(file_path)

        return csharp_file


def extract_public_methods(code_file, code_string):
    language = code_file.get_language()
    top_level_nodes = code_file.get_top_level_nodes()
    logger.debug(f"[extract_public_methods] Full top_level_nodes: {top_level_nodes}")

    all_methods = []
    all_classes = []

    has_dollar_quote = '$"' in code_string
    has_dollar_at_quote = '$@"' in code_string

    match language:
        case LanguageType.PYTHON:
            for node in top_level_nodes:
                if isinstance(node, CodeFunction):
                    all_methods.append((None, node))
                elif isinstance(node, CodeClass):
                    all_classes.append(node)
                    all_methods.extend({(node, f) for f in node.get_methods()})
            return [
                (c, func)
                for c, func in all_methods
                if func.get_access_modifier() is AccessModifier.PUBLIC
            ], all_classes
        case LanguageType.JAVA:
            if isinstance(top_level_nodes, dict):
                classes = top_level_nodes.get("classes", [])
                methods = top_level_nodes.get("methods", [])
                for cls in classes:
                    # pprint(vars(cls))
                    # sys.stdout.flush()
                    all_classes.append(cls)

                    # class_methods = getattr(cls, 'methods', [])
                    class_methods = cls["methods"]
                    for method in class_methods:
                        all_methods.append((cls, method))
                        logger.debug(
                            f"[extract_public_methods] Added method from class {cls['name']}: {method['name']}"
                        )

                # for method in methods:
                #     all_methods.append((None, method))
                #     print(f"Added method: {method['name']}")

            # public_methods = [
            #     (c, func) for c, func in all_methods
            #     if hasattr(func, 'modifiers') and 'public' in func.modifiers
            # ]
            public_methods = all_methods
            return public_methods, all_classes

        case LanguageType.CSHARP:
            if isinstance(top_level_nodes, dict):
                classes = top_level_nodes.get("classes", [])
                methods = top_level_nodes.get("methods", [])
                for cls in classes:
                    # pprint(vars(cls))
                    # sys.stdout.flush()
                    if has_dollar_at_quote:
                        cls["code"] = cls["code"].replace('"$@', '$@"')
                    if has_dollar_quote:
                        cls["code"] = cls["code"].replace('"$', '$"')
                    all_classes.append(cls)

                    # class_methods = getattr(cls, 'methods', [])
                    class_methods = cls["methods"]
                    for method in class_methods:
                        if has_dollar_at_quote:
                            method["code"] = method["code"].replace('"$@', '$@"')
                        if has_dollar_quote:
                            method["code"] = method["code"].replace('"$', '$"')

                        all_methods.append((cls, method))
                        logger.debug(
                            f"[extract_public_methods] Added method from class {cls['name']}: {method['name']}"
                        )

                for method in methods:
                    if has_dollar_at_quote:
                        method["code"] = method["code"].replace('"$@', '$@"')
                    if has_dollar_quote:
                        method["code"] = method["code"].replace('"$', '$"')

                    if not any(m[1]["name"] == method["name"] for m in all_methods):
                        all_methods.append((None, method))
                        logger.debug(f"[extract_public_methods] Added method: {method['name']}")
                    else:
                        logger.debug(
                            f"[extract_public_methods] Skipped method: {method['name']}, already added"
                        )

            # public_methods = [
            #     (c, func) for c, func in all_methods
            #     if hasattr(func, 'modifiers') and 'public' in func.modifiers
            # ]
            public_methods = all_methods
            return public_methods, all_classes

        case _:
            logger.warning(f"[extract_public_methods] Unsupported language: {language}")
            return [], []


def get_code_analysis_result(file_path, code_string):
    code_file = get_code_file(file_path, code_string)
    public_methods, classes = extract_public_methods(code_file, code_string)
    language = code_file.get_language()

    public_methods_dicts = [
        {
            "name": func["name"] if language != "Python" else func.name,
            "object": func,
            "parent": cls,
        }
        for cls, func in public_methods
    ]
    return code_file, public_methods_dicts, classes
