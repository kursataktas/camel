# =========== Copyright 2023 @ CAMEL-AI.org. All Rights Reserved. ===========
# Licensed under the Apache License, Version 2.0 (the “License”);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =========== Copyright 2023 @ CAMEL-AI.org. All Rights Reserved. ===========
import warnings
from inspect import Parameter, signature, getsource
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from docstring_parser import parse
from jsonschema.exceptions import SchemaError
from jsonschema.validators import Draft202012Validator as JSONValidator
from pydantic import create_model
from pydantic.fields import FieldInfo

from camel.utils import get_pydantic_object_schema, to_pascal
from camel.models.base_model import BaseModelBackend
from camel.models import ModelFactory
from camel.types import ModelPlatformType, ModelType
from camel.configs import ChatGPTConfig
from camel.messages import BaseMessage
from camel.agents import ChatAgent


def _remove_a_key(d: Dict, remove_key: Any) -> None:
    r"""Remove a key from a dictionary recursively."""
    if isinstance(d, dict):
        for key in list(d.keys()):
            if key == remove_key:
                del d[key]
            else:
                _remove_a_key(d[key], remove_key)


def get_openai_function_schema(func: Callable) -> Dict[str, Any]:
    r"""Generates a schema dict for an OpenAI function based on its signature.

    This function is deprecated and will be replaced by
    :obj:`get_openai_tool_schema()` in future versions. It parses the
    function's parameters and docstring to construct a JSON schema-like
    dictionary.

    Args:
        func (Callable): The OpenAI function to generate the schema for.

    Returns:
        Dict[str, Any]: A dictionary representing the JSON schema of the
            function, including its name, description, and parameter
            specifications.
    """
    openai_function_schema = get_openai_tool_schema(func)["function"]
    return openai_function_schema


def get_openai_tool_schema(func: Callable) -> Dict[str, Any]:
    r"""Generates an OpenAI JSON schema from a given Python function.

    This function creates a schema compatible with OpenAI's API specifications,
    based on the provided Python function. It processes the function's
    parameters, types, and docstrings, and constructs a schema accordingly.

    Note:
        - Each parameter in `func` must have a type annotation; otherwise, it's
          treated as 'Any'.
        - Variable arguments (*args) and keyword arguments (**kwargs) are not
          supported and will be ignored.
        - A functional description including a brief and detailed explanation
          should be provided in the docstring of `func`.
        - All parameters of `func` must be described in its docstring.
        - Supported docstring styles: ReST, Google, Numpydoc, and Epydoc.

    Args:
        func (Callable): The Python function to be converted into an OpenAI
                         JSON schema.

    Returns:
        Dict[str, Any]: A dictionary representing the OpenAI JSON schema of
                        the provided function.

    See Also:
        `OpenAI API Reference
            <https://platform.openai.com/docs/api-reference/assistants/object>`_
    """
    params: Mapping[str, Parameter] = signature(func).parameters
    fields: Dict[str, Tuple[type, FieldInfo]] = {}
    for param_name, p in params.items():
        param_type = p.annotation
        param_default = p.default
        param_kind = p.kind
        param_annotation = p.annotation
        # Variable parameters are not supported
        if (
            param_kind == Parameter.VAR_POSITIONAL
            or param_kind == Parameter.VAR_KEYWORD
        ):
            continue
        # If the parameter type is not specified, it defaults to typing.Any
        if param_annotation is Parameter.empty:
            param_type = Any
        # Check if the parameter has a default value
        if param_default is Parameter.empty:
            fields[param_name] = (param_type, FieldInfo())
        else:
            fields[param_name] = (param_type, FieldInfo(default=param_default))

    # Applying `create_model()` directly will result in a mypy error,
    # create an alias to avoid this.
    def _create_mol(name, field):
        return create_model(name, **field)

    model = _create_mol(to_pascal(func.__name__), fields)
    parameters_dict = get_pydantic_object_schema(model)
    # The `"title"` is generated by `model.model_json_schema()`
    # but is useless for openai json schema
    _remove_a_key(parameters_dict, "title")

    docstring = parse(func.__doc__ or "")
    for param in docstring.params:
        if (name := param.arg_name) in parameters_dict["properties"] and (
            description := param.description
        ):
            parameters_dict["properties"][name]["description"] = description

    short_description = docstring.short_description or ""
    long_description = docstring.long_description or ""
    if long_description:
        func_description = f"{short_description}\n{long_description}"
    else:
        func_description = short_description

    openai_function_schema = {
        "name": func.__name__,
        "description": func_description,
        "parameters": parameters_dict,
    }

    openai_tool_schema = {
        "type": "function",
        "function": openai_function_schema,
    }
    return openai_tool_schema

def generate_docstring(
    code: str, 
    model: BaseModelBackend,
) -> str:
    """Generates a docstring for a given function code using LLM.

    Args:
        code (str): The source code of the function.
        model (BaseModelBackend): The model used for generating the docstring.

    Returns:
        str: The generated docstring.
    """
    # Create the docstring prompt
    docstring_prompt = '''
    **Role**: Generate professional Python docstrings conforming to 
    PEP 8/PEP 257.

    **Requirements**:
    - Use appropriate format: reST, Google, or NumPy, as needed.
    - Include parameters, return values, and exceptions.
    - Reference any existing docstring in the function and 
      retain useful information.

    **Input**: Python function.

    **Output**: Docstring content (plain text, no code markers).

    **Example:**

    Input:
    ```python
    def add(a: int, b: int) -> int:
        return a + b
    ```

    Output:
    Adds two numbers.
    Args:
        a (int): The first number.
        b (int): The second number.

    Returns:
        int: The sum of the two numbers.

    **Task**: Generate a docstring for the function below.

    '''
    # Initialize assistant with system message and model
    assistant_sys_msg = BaseMessage.make_assistant_message(
        role_name="Assistant",
        content="You are a helpful assistant.",
    )
    docstring_assistant = ChatAgent(
        assistant_sys_msg, 
        model=model, 
        token_limit=4096
    )

    # Create user message to prompt the assistant
    user_msg = BaseMessage.make_user_message(
        role_name="User",
        content=docstring_prompt + code,
    )
    
    # Get the response containing the generated docstring
    response = docstring_assistant.step(user_msg)
    return response.msg.content

class FunctionTool:
    r"""An abstraction of a function that OpenAI chat models can call. See
    https://platform.openai.com/docs/api-reference/chat/create.

    By default, the tool schema will be parsed from the func, or you can
    provide a user-defined tool schema to override.

    Args:
        func (Callable): The function to call.The tool schema is parsed from
            the signature and docstring by default.
        openai_tool_schema (Optional[Dict[str, Any]], optional): A user-defined
            openai tool schema to override the default result.
            (default: :obj:`None`)
        schema_assistant (Optional[BaseModelBackend], optional): An assistant 
            (e.g., an LLM model) used to generate the schema if no valid 
            schema is provided and use_schema_assistant is enabled.
            (default: :obj:`None`)
        use_schema_assistant (bool, optional): Whether to enable the use of 
            the schema_assistant to automatically generate the schema if 
            validation fails or no valid schema is provided.
            (default: :obj:`False`)
    """

    def __init__(
        self,
        func: Callable,
        openai_tool_schema: Optional[Dict[str, Any]] = None,
        schema_assistant: Optional[BaseModelBackend] = None,
        use_schema_assistant: bool = False,
    ) -> None:
        self.func = func
        self.openai_tool_schema = openai_tool_schema

        if self.openai_tool_schema is not None:
            self.openai_tool_schema = get_openai_tool_schema(func)

            if use_schema_assistant:
                try:
                    self.validate_openai_tool_schema(self.openai_tool_schema)
                except Exception as e:
                    print(
                        f"Warning: No valid schema found "
                        f"for {self.func.__name__}. "
                        f"Attempting to generate one using LLM."
                    )
                    schema = self.generate_openai_tool_schema(
                        schema_assistant
                    )
                    if schema:
                        self.openai_tool_schema = schema
                    else:
                        raise ValueError(
                            f"Failed to generate valid schema for "
                            f"{self.func.__name__}"
                        )


    @staticmethod
    def validate_openai_tool_schema(
        openai_tool_schema: Dict[str, Any],
    ) -> None:
        r"""Validates the OpenAI tool schema against
        :obj:`ToolAssistantToolsFunction`.
        This function checks if the provided :obj:`openai_tool_schema` adheres
        to the specifications required by OpenAI's
        :obj:`ToolAssistantToolsFunction`. It ensures that the function
        description and parameters are correctly formatted according to JSON
        Schema specifications.
        Args:
            openai_tool_schema (Dict[str, Any]): The OpenAI tool schema to
                validate.
        Raises:
            ValidationError: If the schema does not comply with the
                specifications.
            ValueError: If the function description or parameter descriptions
                are missing in the schema.
            SchemaError: If the parameters do not meet JSON Schema reference
                specifications.
        """
        # Check the type
        if not openai_tool_schema["type"]:
            raise ValueError("miss type")
        # Check the function description
        if not openai_tool_schema["function"]["description"]:
            raise ValueError("miss function description")

        # Validate whether parameters
        # meet the JSON Schema reference specifications.
        # See https://platform.openai.com/docs/guides/gpt/function-calling
        # for examples, and the
        # https://json-schema.org/understanding-json-schema/ for
        # documentation about the format.
        parameters = openai_tool_schema["function"]["parameters"]
        try:
            JSONValidator.check_schema(parameters)
        except SchemaError as e:
            raise e
        # Check the parameter description
        properties: Dict[str, Any] = parameters["properties"]
        for param_name in properties.keys():
            param_dict = properties[param_name]
            if "description" not in param_dict:
                raise ValueError(
                    f'miss description of parameter "{param_name}"'
                )

    def get_openai_tool_schema(self) -> Dict[str, Any]:
        r"""Gets the OpenAI tool schema for this function.

        This method returns the OpenAI tool schema associated with this
        function, after validating it to ensure it meets OpenAI's
        specifications.

        Returns:
            Dict[str, Any]: The OpenAI tool schema for this function.
        """
        self.validate_openai_tool_schema(self.openai_tool_schema)
        return self.openai_tool_schema

    def set_openai_tool_schema(self, schema: Dict[str, Any]) -> None:
        r"""Sets the OpenAI tool schema for this function.

        Allows setting a custom OpenAI tool schema for this function.

        Args:
            schema (Dict[str, Any]): The OpenAI tool schema to set.
        """
        self.openai_tool_schema = schema

    def get_openai_function_schema(self) -> Dict[str, Any]:
        r"""Gets the schema of the function from the OpenAI tool schema.

        This method extracts and returns the function-specific part of the
        OpenAI tool schema associated with this function.

        Returns:
            Dict[str, Any]: The schema of the function within the OpenAI tool
                schema.
        """
        self.validate_openai_tool_schema(self.openai_tool_schema)
        return self.openai_tool_schema["function"]

    def set_openai_function_schema(
        self,
        openai_function_schema: Dict[str, Any],
    ) -> None:
        r"""Sets the schema of the function within the OpenAI tool schema.

        Args:
            openai_function_schema (Dict[str, Any]): The function schema to set
                within the OpenAI tool schema.
        """
        self.openai_tool_schema["function"] = openai_function_schema

    def get_function_name(self) -> str:
        r"""Gets the name of the function from the OpenAI tool schema.

        Returns:
            str: The name of the function.
        """
        self.validate_openai_tool_schema(self.openai_tool_schema)
        return self.openai_tool_schema["function"]["name"]

    def set_function_name(self, name: str) -> None:
        r"""Sets the name of the function in the OpenAI tool schema.

        Args:
            name (str): The name of the function to set.
        """
        self.openai_tool_schema["function"]["name"] = name

    def get_function_description(self) -> str:
        r"""Gets the description of the function from the OpenAI tool
        schema.

        Returns:
            str: The description of the function.
        """
        self.validate_openai_tool_schema(self.openai_tool_schema)
        return self.openai_tool_schema["function"]["description"]

    def set_function_description(self, description: str) -> None:
        r"""Sets the description of the function in the OpenAI tool schema.

        Args:
            description (str): The description for the function.
        """
        self.openai_tool_schema["function"]["description"] = description

    def get_paramter_description(self, param_name: str) -> str:
        r"""Gets the description of a specific parameter from the function
        schema.

        Args:
            param_name (str): The name of the parameter to get the
                description.

        Returns:
            str: The description of the specified parameter.
        """
        self.validate_openai_tool_schema(self.openai_tool_schema)
        return self.openai_tool_schema["function"]["parameters"]["properties"][
            param_name
        ]["description"]

    def set_paramter_description(
        self,
        param_name: str,
        description: str,
    ) -> None:
        r"""Sets the description for a specific parameter in the function
        schema.

        Args:
            param_name (str): The name of the parameter to set the description
                for.
            description (str): The description for the parameter.
        """
        self.openai_tool_schema["function"]["parameters"]["properties"][
            param_name
        ]["description"] = description

    def get_parameter(self, param_name: str) -> Dict[str, Any]:
        r"""Gets the schema for a specific parameter from the function schema.

        Args:
            param_name (str): The name of the parameter to get the schema.

        Returns:
            Dict[str, Any]: The schema of the specified parameter.
        """
        self.validate_openai_tool_schema(self.openai_tool_schema)
        return self.openai_tool_schema["function"]["parameters"]["properties"][
            param_name
        ]

    def set_parameter(self, param_name: str, value: Dict[str, Any]):
        r"""Sets the schema for a specific parameter in the function schema.

        Args:
            param_name (str): The name of the parameter to set the schema for.
            value (Dict[str, Any]): The schema to set for the parameter.
        """
        try:
            JSONValidator.check_schema(value)
        except SchemaError as e:
            raise e
        self.openai_tool_schema["function"]["parameters"]["properties"][
            param_name
        ] = value
    
    def generate_openai_tool_schema(
        self,
        schema_assistant: Optional[BaseModelBackend]=None,
    ) -> Dict[str, Any]:
        r"""Generates an OpenAI tool schema for the specified function.

        This method generates the OpenAI tool schema using the provided 
        LLM assistant. If no assistant is provided, it defaults 
        to creating a GPT_4O_MINI model. The function's source code is used
        to generate a docstring and schema, which are validated before 
        returning the final schema. If schema generation or validation fails, 
        the process retries up to two times.

        Args:
            schema_assistant (Optional[BaseModelBackend]): An optional 
            assistant model to use for schema generation. If not provided, a 
            GPT_4O_MINI model will be created.

        Returns:
            Dict[str, Any]: The generated OpenAI tool schema for the function.

        Raises:
            ValueError: If schema generation or validation fails after the 
            maximum number of retries, a ValueError is raised, 
            prompting manual schema setting.
        """
        if not schema_assistant:
            print(
                f"Warning: No model provided. "
                f"Use GPT_4O_MINI to generate the schema."
            )
            try:
                schema_assistant = ModelFactory.create(
                    model_platform=ModelPlatformType.OPENAI,
                    model_type=ModelType.GPT_4O_MINI,
                    model_config_dict=ChatGPTConfig(temperature=1.0).as_dict()
                )
            except Exception as e:
                raise ValueError(
                    f"Failed to generate the OpenAI tool schema for "
                    f"the function {self.func.__name__}. "
                    f"Please set the OpenAI tool schema manually."
                ) from e

        function_string = getsource(self.func)
        
        max_retries = 2
        retries = 0

        # Retry loop to handle schema generation and validation
        while retries < max_retries:
            try:
                # Generate the docstring and the schema
                docstring = generate_docstring(
                    function_string, 
                    schema_assistant
                )
                self.func.__doc__ = docstring
                schema = get_openai_tool_schema(self.func)

                # Validate the schema
                self.validate_openai_tool_schema(schema)

                print(
                    f"Successfully generated the OpenAI tool schema for "
                    f"the function {self.func.__name__}."
                )
                return schema

            except Exception as e:
                retries += 1
                if retries == max_retries:
                    raise ValueError(
                        f"Failed to generate the OpenAI tool Schema. "
                        f"Please set the OpenAI tool schema for "
                        f"function {self.func.__name__} manually."
                    ) from e
                print(f"Schema validation failed. Retrying...")


    @property
    def parameters(self) -> Dict[str, Any]:
        r"""Getter method for the property :obj:`parameters`.

        Returns:
            Dict[str, Any]: the dictionary containing information of
                parameters of this function.
        """
        self.validate_openai_tool_schema(self.openai_tool_schema)
        return self.openai_tool_schema["function"]["parameters"]["properties"]

    @parameters.setter
    def parameters(self, value: Dict[str, Any]) -> None:
        r"""Setter method for the property :obj:`parameters`. It will
        firstly check if the input parameters schema is valid. If invalid,
        the method will raise :obj:`jsonschema.exceptions.SchemaError`.

        Args:
            value (Dict[str, Any]): the new dictionary value for the
                function's parameters.
        """
        try:
            JSONValidator.check_schema(value)
        except SchemaError as e:
            raise e
        self.openai_tool_schema["function"]["parameters"]["properties"] = value


warnings.simplefilter('always', DeprecationWarning)


# Alias for backwards compatibility
class OpenAIFunction(FunctionTool):
    def __init__(self, *args, **kwargs):
        PURPLE = '\033[95m'
        RESET = '\033[0m'

        def purple_warning(msg):
            warnings.warn(
                PURPLE + msg + RESET, DeprecationWarning, stacklevel=2
            )

        purple_warning(
            "OpenAIFunction is deprecated, please use FunctionTool instead."
        )
        super().__init__(*args, **kwargs)
