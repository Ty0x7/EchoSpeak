"""@tool: turn a function into an EchoSpeak tool. Replaces LangChain's decorator of the same name.

EchoSpeak used four things from LangChain's StructuredTool: a name, a
description, an argument schema (a pydantic model, written by hand or built from
the function's signature) and invoke(arguments), which validates the arguments
against that schema before calling the function. This is exactly that, so the
lean runtime, the tool registry, skills and connections keep working unchanged.

    @tool(args_schema=OpenApplicationArgs, description="Launch a permitted app.")
    def open_application(app: str, args: Optional[str] = None) -> str: ...

    open_application.invoke({"app": "notepad"})   # validated, like before
    open_application("notepad")                    # a plain call still works
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional, Union

from pydantic import BaseModel, ConfigDict, create_model


class Tool:
    def __init__(self, func: Callable[..., Any], *, name: str, description: str,
                 args_schema: Optional[type[BaseModel]]) -> None:
        self.func = func
        self.name = name
        self.description = description
        self.args_schema = args_schema
        self.__doc__ = func.__doc__
        self.__wrapped__ = func
        params = inspect.signature(func).parameters.values()
        self._accepts = {p.name for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
        self._takes_any = any(p.kind is p.VAR_KEYWORD for p in params)

    def _arguments(self, data: Union[dict[str, Any], str, None]) -> dict[str, Any]:
        if data is None:
            data = {}
        if not isinstance(data, dict):
            fields = list(self.args_schema.model_fields) if self.args_schema else []
            if len(fields) != 1:
                raise TypeError(f"{self.name} takes named arguments, not one value")
            data = {fields[0]: data}  # a single-argument tool may get its value bare
        if self.args_schema is None:
            return dict(data)
        model = self.args_schema.model_validate(data)
        values = {name: getattr(model, name) for name in type(model).model_fields}
        return values if self._takes_any else {k: v for k, v in values.items() if k in self._accepts}

    def invoke(self, input: Union[dict[str, Any], str, None] = None, config: Any = None, **_: Any) -> Any:  # noqa: A002
        """Validate the arguments against the schema, then run the function."""
        return self.func(**self._arguments(input))

    def run(self, tool_input: Union[dict[str, Any], str, None] = None, **kwargs: Any) -> Any:
        return self.invoke(tool_input, **kwargs)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"


def _schema_from_signature(func: Callable[..., Any]) -> type[BaseModel]:
    fields: dict[str, Any] = {}
    hints = inspect.get_annotations(func, eval_str=True) if hasattr(inspect, "get_annotations") else {}
    for param in inspect.signature(func).parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        annotation = hints.get(param.name, Any)
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[param.name] = (annotation, default)
    return create_model(f"{func.__name__}_args", __config__=ConfigDict(arbitrary_types_allowed=True), **fields)


def tool(func: Optional[Callable[..., Any]] = None, *, args_schema: Optional[type[BaseModel]] = None,
         description: str = "", name: str = "") -> Any:
    """Use as @tool, @tool(description=...), or @tool(args_schema=Model, description=...)."""
    def wrap(target: Callable[..., Any]) -> Tool:
        text = description or inspect.getdoc(target) or ""
        return Tool(target, name=name or target.__name__, description=text,
                    args_schema=args_schema or _schema_from_signature(target))
    return wrap(func) if callable(func) else wrap
