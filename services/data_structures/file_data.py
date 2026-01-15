import traceback
import uuid
from dataclasses import dataclass
from typing import Optional
from app.services.code_analysis.code_analysis import get_code_analysis_result
from app.utils.constants import LanguageType
from sentan_tools.code import CodeFunction, CodeClass


@dataclass
class ClassData:
    id: str
    name: str
    class_obj: CodeClass
    language: str

    @staticmethod
    def from_obj(data, class_id="", language="Python"):
        if language == "Python":
            class_id = class_id or data.name + "-" + str(uuid.uuid1())
            return ClassData(class_id, data.name, data, language)
        else:
            class_id = class_id or data["name"] + "-" + str(uuid.uuid1())
            return ClassData(class_id, data["name"], data, language)

    def get_code(self):
        match self.language:
            case LanguageType.PYTHON:
                return self.class_obj.get_code()
            case LanguageType.JAVA:
                return self.class_obj["code"]
            case LanguageType.CSHARP:
                return self.class_obj["code"]


@dataclass
class FunctionData:
    id: str
    name: str
    function_obj: CodeFunction
    language: str
    parent: Optional[CodeClass]

    @staticmethod
    def from_dict(data_dict, language="python", func_id=""):
        func_id = func_id or data_dict["name"] + "-" + str(uuid.uuid1())
        return FunctionData(
            id=func_id,
            name=data_dict["name"],
            function_obj=data_dict["object"],
            language=language,
            parent=data_dict["parent"],
        )

    def get_code(self):
        match self.language:
            case LanguageType.PYTHON:
                return self.function_obj.get_code()
            case LanguageType.JAVA:
                return self.function_obj["code"]
            case LanguageType.CSHARP:
                return self.function_obj["code"]


class FileData:

    def __init__(self, file_path, file_contents):
        self.file_path = file_path
        try:
            self.code_file, pub_methods, class_dict = get_code_analysis_result(
                file_path, file_contents
            )
        except Exception as e:
            traceback.print_exc()

            trimmed_path = "/".join(file_path.split("/")[1:])
            error_message = f"{trimmed_path}ファイルの記載を修正してください"

            raise ValueError(error_message)

        self.language = self.code_file.get_language()
        self.class_data = [
            ClassData.from_obj(c, str(i), self.language)
            for i, c in enumerate(class_dict)
        ]
        self.function_data = [
            FunctionData.from_dict(m, self.language, func_id=str(i) + m["name"])
            for i, m in enumerate(pub_methods)
        ]

    def get_code_functions_dict(self):
        return [
            {
                "id": func.id,
                "name": func.name,
                "code": func.get_code(),
                "class_id": (
                    None
                    if not func.parent
                    else [c for c in self.class_data if func.parent == c.class_obj][
                        0
                    ].id
                ),
                "class_name": (
                    func.parent["name"]
                    if func.parent and self.language != "Python"
                    else func.parent.name if func.parent else ""
                ),
            }
            for func in self.function_data
        ]

    def get_code_classes_dict(self):
        return [
            {
                "id": c.id,
                "name": c.name,
                "code": c.get_code(),
            }
            for c in self.class_data
        ]

    def get_code(self):
        return self.code_file.get_code()

    def get_language(self):
        return self.code_file.get_language()
