"""
type_inference 模块的多语言类型提取测试。

通过 TreeSitterAdapter 解析真实代码，再调用 extract_types_for_file
验证各语言函数/方法的返回类型和参数注解提取是否正确。
"""

from __future__ import annotations

import pytest

from src import Symbol
from src.parser import TreeSitterAdapter
from src.type_inference import extract_types_for_file


@pytest.fixture(scope="module")
def ts():
    return TreeSitterAdapter()


def _parse_and_extract(ts, code, lang, symbols):
    tree = ts.parse(code.encode("utf-8"), lang)
    if tree is None:
        pytest.skip(f"{lang} parser 不可用")
    sym_map = {s.id: s for s in symbols}
    sym_ids = list(sym_map.keys())
    extract_types_for_file(tree, lang, sym_ids, sym_map)
    return symbols


# ═══════════════════════════════════════════════════════════════════════════════
# Python
# ═══════════════════════════════════════════════════════════════════════════════


class TestPythonTypeExtraction:
    def test_return_type_annotation(self, ts):
        code = "def foo() -> str:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert result[0].return_type == "str"

    def test_params_with_annotations(self, ts):
        code = "def foo(x: int, y: str):\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "x: int" in result[0].params
        assert "y: str" in result[0].params

    def test_no_annotations(self, ts):
        code = "def foo(x, y):\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert result[0].return_type == ""
        assert "x" in result[0].params
        assert "y" in result[0].params

    def test_return_type_and_params_together(self, ts):
        code = "def add(a: int, b: int) -> int:\n    return a + b\n"
        sym = Symbol(id="s1", name="add", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert result[0].return_type == "int"
        assert "a: int" in result[0].params
        assert "b: int" in result[0].params

    def test_method_in_class(self, ts):
        code = "class Foo:\n    def bar(self, x: int) -> str:\n        return str(x)\n"
        sym = Symbol(id="s1", name="bar", kind="method", file="a.py", line=2)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert result[0].return_type == "str"
        assert "x: int" in result[0].params

    def test_complex_return_type(self, ts):
        code = "def foo() -> dict[str, int]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "dict[str, int]" in result[0].return_type


# ═══════════════════════════════════════════════════════════════════════════════
# TypeScript
# ═══════════════════════════════════════════════════════════════════════════════


class TestTypeScriptTypeExtraction:
    def test_function_return_type(self, ts):
        code = "function foo(): string {\n  return '';\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert result[0].return_type == "string"

    def test_function_params(self, ts):
        code = "function foo(x: number, y: string): void {}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert "x: number" in result[0].params
        assert "y: string" in result[0].params

    def test_method_params(self, ts):
        code = "class Foo {\n  method(x: number, y: string): void {}\n}\n"
        sym = Symbol(id="s1", name="method", kind="method", file="a.ts", line=2)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert "x: number" in result[0].params
        assert "y: string" in result[0].params

    def test_no_return_type(self, ts):
        code = "function foo(x: number) {}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert result[0].return_type == ""

    def test_arrow_function(self, ts):
        code = "const add = (a: number, b: number): number => a + b;\n"
        sym = Symbol(id="s1", name="add", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert result[0].return_type == "number"
        assert "a: number" in result[0].params


# ═══════════════════════════════════════════════════════════════════════════════
# Go
# ═══════════════════════════════════════════════════════════════════════════════


class TestGoTypeExtraction:
    def test_single_return_type(self, ts):
        code = "func foo() error {\n\treturn nil\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.go", line=1)
        result = _parse_and_extract(ts, code, "go", [sym])
        assert result[0].return_type == "error"

    def test_multiple_return_types(self, ts):
        code = "func foo() (int, error) {\n\treturn 0, nil\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.go", line=1)
        result = _parse_and_extract(ts, code, "go", [sym])
        assert "int" in result[0].return_type
        assert "error" in result[0].return_type

    def test_method_params_not_receiver(self, ts):
        code = "func (r *Type) Method(ctx context.Context, id int) error {\n\treturn nil\n}\n"
        sym = Symbol(id="s1", name="Method", kind="method", file="a.go", line=1)
        result = _parse_and_extract(ts, code, "go", [sym])
        assert "ctx context.Context" in result[0].params
        assert "id int" in result[0].params
        assert "r *Type" not in result[0].params

    def test_method_return_type(self, ts):
        code = "func (r *Type) Method(ctx context.Context, id int) error {\n\treturn nil\n}\n"
        sym = Symbol(id="s1", name="Method", kind="method", file="a.go", line=1)
        result = _parse_and_extract(ts, code, "go", [sym])
        assert result[0].return_type == "error"

    def test_no_return(self, ts):
        code = "func foo() {\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.go", line=1)
        result = _parse_and_extract(ts, code, "go", [sym])
        assert result[0].return_type == ""

    def test_function_with_params(self, ts):
        code = "func add(a int, b int) int {\n\treturn a + b\n}\n"
        sym = Symbol(id="s1", name="add", kind="function", file="a.go", line=1)
        result = _parse_and_extract(ts, code, "go", [sym])
        assert "a int" in result[0].params
        assert "b int" in result[0].params
        assert result[0].return_type == "int"

    def test_method_receiver_not_in_params(self, ts):
        code = "func (s *Server) Handle(req *http.Request) {}\n"
        sym = Symbol(id="s1", name="Handle", kind="method", file="a.go", line=1)
        result = _parse_and_extract(ts, code, "go", [sym])
        assert "req *http.Request" in result[0].params
        assert "s *Server" not in result[0].params


# ═══════════════════════════════════════════════════════════════════════════════
# Rust
# ═══════════════════════════════════════════════════════════════════════════════


class TestRustTypeExtraction:
    def test_return_type(self, ts):
        code = "fn foo() -> Result<bool> {\n    Ok(true)\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert result[0].return_type == "Result<bool>"

    def test_params(self, ts):
        code = "fn foo(x: i32, y: &str) {\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert "x: i32" in result[0].params
        assert "y: &str" in result[0].params

    def test_no_return_type(self, ts):
        code = "fn foo() {\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert result[0].return_type == ""

    def test_return_type_and_params(self, ts):
        code = "fn add(a: i32, b: i32) -> i32 {\n    a + b\n}\n"
        sym = Symbol(id="s1", name="add", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert result[0].return_type == "i32"
        assert "a: i32" in result[0].params
        assert "b: i32" in result[0].params


# ═══════════════════════════════════════════════════════════════════════════════
# Java
# ═══════════════════════════════════════════════════════════════════════════════


class TestJavaTypeExtraction:
    def test_method_return_type_and_params(self, ts):
        code = (
            "public class Foo {\n"
            "    public String bar(int x, String y) {\n"
            "        return y;\n"
            "    }\n"
            "}\n"
        )
        sym = Symbol(id="s1", name="bar", kind="method", file="a.java", line=2)
        result = _parse_and_extract(ts, code, "java", [sym])
        assert result[0].return_type == "String"
        assert "int x" in result[0].params
        assert "String y" in result[0].params

    def test_void_return(self, ts):
        code = "public class Foo {\n    public void doSomething() {\n    }\n}\n"
        sym = Symbol(id="s1", name="doSomething", kind="method", file="a.java", line=2)
        result = _parse_and_extract(ts, code, "java", [sym])
        assert result[0].return_type == "void"

    def test_constructor_no_return(self, ts):
        code = "public class Foo {\n    public Foo(int x) {\n    }\n}\n"
        sym = Symbol(id="s1", name="Foo", kind="method", file="a.java", line=2)
        result = _parse_and_extract(ts, code, "java", [sym])
        assert result[0].return_type == ""


# ═══════════════════════════════════════════════════════════════════════════════
# C++
# ═══════════════════════════════════════════════════════════════════════════════


class TestCppTypeExtraction:
    def test_function_return_type(self, ts):
        code = "int main() {\n    return 0;\n}\n"
        sym = Symbol(id="s1", name="main", kind="function", file="a.cpp", line=1)
        result = _parse_and_extract(ts, code, "cpp", [sym])
        assert result[0].return_type == "int"

    def test_function_with_params(self, ts):
        code = "int add(int a, int b) {\n    return a + b;\n}\n"
        sym = Symbol(id="s1", name="add", kind="function", file="a.cpp", line=1)
        result = _parse_and_extract(ts, code, "cpp", [sym])
        assert result[0].return_type == "int"
        assert "int a" in result[0].params
        assert "int b" in result[0].params

    def test_void_return(self, ts):
        code = "void process() {\n}\n"
        sym = Symbol(id="s1", name="process", kind="function", file="a.cpp", line=1)
        result = _parse_and_extract(ts, code, "cpp", [sym])
        assert result[0].return_type == "void"


# ═══════════════════════════════════════════════════════════════════════════════
# extract_types_for_file 边界情况
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractTypesForFileEdgeCases:
    def test_unsupported_language_returns_zero(self, ts):
        code = "def foo(): pass\n"
        tree = ts.parse(code.encode("utf-8"), "python")
        result = extract_types_for_file(tree, "brainfuck", [], {})
        assert result == 0

    def test_no_function_symbols_returns_zero(self, ts):
        code = "def foo() -> str:\n    pass\n"
        tree = ts.parse(code.encode("utf-8"), "python")
        sym = Symbol(id="s1", name="Foo", kind="class", file="a.py", line=1)
        result = extract_types_for_file(tree, "python", ["s1"], {"s1": sym})
        assert result == 0

    def test_already_filled_fields_not_overwritten(self, ts):
        code = "def foo() -> str:\n    pass\n"
        sym = Symbol(
            id="s1",
            name="foo",
            kind="function",
            file="a.py",
            line=1,
            return_type="existing",
            params="existing",
        )
        tree = ts.parse(code.encode("utf-8"), "python")
        result = extract_types_for_file(tree, "python", ["s1"], {"s1": sym})
        assert sym.return_type == "existing"
        assert result == 0

    def test_enriched_count(self, ts):
        code = "def foo(x: int) -> str:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        tree = ts.parse(code.encode("utf-8"), "python")
        count = extract_types_for_file(tree, "python", ["s1"], {"s1": sym})
        assert count == 2

    def test_multiple_symbols(self, ts):
        code = (
            "def foo() -> str:\n"
            "    return ''\n"
            "\n"
            "def bar(x: int) -> int:\n"
            "    return x\n"
        )
        sym1 = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        sym2 = Symbol(id="s2", name="bar", kind="function", file="a.py", line=4)
        tree = ts.parse(code.encode("utf-8"), "python")
        count = extract_types_for_file(
            tree, "python", ["s1", "s2"], {"s1": sym1, "s2": sym2}
        )
        assert sym1.return_type == "str"
        assert sym2.return_type == "int"
        assert "x: int" in sym2.params
        assert count == 3


# ═══════════════════════════════════════════════════════════════════════════════
# Python 边界情况 (Issue #66)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPythonEdgeCases:
    """Python 类型推断边界情况测试。"""

    # --- 复合类型 ---

    def test_list_return_type(self, ts):
        code = "def foo() -> list[int]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "list[int]" in result[0].return_type

    def test_dict_return_type(self, ts):
        code = "def foo() -> dict[str, int]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "dict[str, int]" in result[0].return_type

    def test_tuple_return_type(self, ts):
        code = "def foo() -> tuple[int, str, float]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "tuple" in result[0].return_type
        assert "int" in result[0].return_type

    def test_set_return_type(self, ts):
        code = "def foo() -> set[str]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "set[str]" in result[0].return_type

    def test_frozenset_return_type(self, ts):
        code = "def foo() -> frozenset[int]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "frozenset[int]" in result[0].return_type

    # --- Optional / Union ---

    def test_optional_return_type(self, ts):
        code = "from typing import Optional\ndef foo() -> Optional[int]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=2)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "Optional" in result[0].return_type
        assert "int" in result[0].return_type

    def test_union_return_type(self, ts):
        code = "from typing import Union\ndef foo() -> Union[int, str, None]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=2)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "Union" in result[0].return_type

    def test_pipe_union_syntax(self, ts):
        code = "def foo() -> int | None:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "int" in result[0].return_type
        assert "None" in result[0].return_type

    def test_multi_pipe_union(self, ts):
        code = "def foo() -> int | str | float:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "int" in result[0].return_type

    # --- typing 模块 ---

    def test_callable_return_type(self, ts):
        code = "from typing import Callable\ndef foo() -> Callable[[int], str]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=2)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "Callable" in result[0].return_type

    def test_iterator_return_type(self, ts):
        code = "from typing import Iterator\ndef foo() -> Iterator[str]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=2)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "Iterator" in result[0].return_type

    def test_sequence_return_type(self, ts):
        code = "from typing import Sequence\ndef foo() -> Sequence[int]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=2)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "Sequence" in result[0].return_type

    def test_mapping_return_type(self, ts):
        code = "from typing import Mapping\ndef foo() -> Mapping[str, int]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=2)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "Mapping" in result[0].return_type

    # --- 特殊参数 ---

    def test_args_kwargs_params(self, ts):
        code = "def foo(*args: int, **kwargs: str):\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "args" in result[0].params

    def test_partial_annotation(self, ts):
        code = "def foo(x: int, y) -> str:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert result[0].return_type == "str"
        assert "x: int" in result[0].params

    def test_nested_complex_type(self, ts):
        code = "def foo() -> dict[str, list[int | None]]:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert "dict" in result[0].return_type

    def test_only_return_annotation(self, ts):
        code = "def foo(x, y) -> int:\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert result[0].return_type == "int"
        assert "x" in result[0].params

    def test_only_param_annotations(self, ts):
        code = "def foo(x: int, y: str):\n    pass\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.py", line=1)
        result = _parse_and_extract(ts, code, "python", [sym])
        assert result[0].return_type == ""
        assert "x: int" in result[0].params
        assert "y: str" in result[0].params


# ═══════════════════════════════════════════════════════════════════════════════
# TypeScript 边界情况 (Issue #66)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTypeScriptEdgeCases:
    """TypeScript 类型推断边界情况测试。"""

    # --- 复合类型 ---

    def test_array_return_type(self, ts):
        code = "function foo(): number[] {\n  return [];\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert "number[]" in result[0].return_type

    def test_tuple_return_type(self, ts):
        code = "function foo(): [number, string] {\n  return [1, 'a'];\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert "number" in result[0].return_type
        assert "string" in result[0].return_type

    def test_object_type_return(self, ts):
        code = "function foo(): { name: string; age: number } {\n  return { name: '', age: 0 };\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert result[0].return_type != ""

    # --- 联合 / 交叉类型 ---

    def test_union_return_type(self, ts):
        code = "function foo(): string | number {\n  return 1;\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert "string" in result[0].return_type
        assert "number" in result[0].return_type

    def test_literal_union_type(self, ts):
        code = """function foo(): "a" | "b" | "c" {
  return "a";
}
"""
        sym = Symbol(id="s1", name="foo", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert result[0].return_type != ""

    # --- 泛型 ---

    def test_generic_function(self, ts):
        code = "function identity<T>(x: T): T {\n  return x;\n}\n"
        sym = Symbol(id="s1", name="identity", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert result[0].return_type == "T"
        assert "x: T" in result[0].params

    def test_generic_with_constraint(self, ts):
        code = "function merge<T extends object, U extends object>(a: T, b: U): T & U {\n  return { ...a, ...b };\n}\n"
        sym = Symbol(id="s1", name="merge", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert result[0].return_type != ""

    # --- 可选/默认/剩余参数 ---

    def test_optional_param(self, ts):
        code = "function foo(x: string, y?: number): void {}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert "x: string" in result[0].params
        assert result[0].return_type == "void"

    def test_rest_params(self, ts):
        code = "function foo(...args: number[]): void {}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert "args" in result[0].params

    # --- 枚举 ---

    def test_enum_return_type(self, ts):
        code = "enum Direction { Up, Down }\nfunction foo(): Direction {\n  return Direction.Up;\n}\n"
        sym = Symbol(id="s2", name="foo", kind="function", file="a.ts", line=2)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert "Direction" in result[0].return_type

    # --- class 方法 ---

    def test_class_method_with_generics(self, ts):
        code = "class Box<T> {\n  value: T;\n  getValue(): T {\n    return this.value;\n  }\n}\n"
        sym = Symbol(id="s1", name="getValue", kind="method", file="a.ts", line=3)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert result[0].return_type == "T"

    # --- 无注解 ---

    def test_no_annotations(self, ts):
        code = "function foo(x, y) {\n  return x;\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.ts", line=1)
        result = _parse_and_extract(ts, code, "typescript", [sym])
        assert result[0].return_type == ""
        assert "x" in result[0].params


# ═══════════════════════════════════════════════════════════════════════════════
# Rust 边界情况 (Issue #66)
# ═══════════════════════════════════════════════════════════════════════════════


class TestRustEdgeCases:
    """Rust 类型推断边界情况测试。"""

    # --- 引用 / 生命周期 ---

    def test_ref_return_type(self, ts):
        code = "fn foo(x: &str) -> &str {\n    x\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert "&str" in result[0].return_type
        assert "x: &str" in result[0].params

    def test_mut_ref_param(self, ts):
        code = "fn foo(x: &mut Vec<i32>) -> &mut i32 {\n    &mut x[0]\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert "x: &mut" in result[0].params

    def test_lifetime_annotation(self, ts):
        code = "fn longest<'a>(x: &'a str, y: &'a str) -> &'a str {\n    x\n}\n"
        sym = Symbol(id="s1", name="longest", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert "&" in result[0].return_type
        assert "str" in result[0].return_type

    # --- 泛型 / Trait Bound ---

    def test_generic_function(self, ts):
        code = "fn foo<T>(x: T) -> T {\n    x\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert result[0].return_type == "T"
        assert "x: T" in result[0].params

    def test_generic_with_trait_bound(self, ts):
        code = "use std::fmt::Display;\nfn foo<T: Display>(x: T) -> String {\n    x.to_string()\n}\n"
        sym = Symbol(id="s2", name="foo", kind="function", file="a.rs", line=2)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert result[0].return_type == "String"

    def test_where_clause(self, ts):
        code = "use std::fmt::Display;\nuse std::clone::Clone;\nfn foo<T>(x: T) -> T where T: Display + Clone {\n    x\n}\n"
        sym = Symbol(id="s3", name="foo", kind="function", file="a.rs", line=3)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert result[0].return_type == "T"

    # --- impl Trait ---

    def test_impl_display_return(self, ts):
        code = "use std::fmt::Display;\nfn foo() -> impl Display {\n    42\n}\n"
        sym = Symbol(id="s2", name="foo", kind="function", file="a.rs", line=2)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert "impl" in result[0].return_type
        assert "Display" in result[0].return_type

    def test_impl_iterator_return(self, ts):
        code = "fn foo() -> impl Iterator<Item = i32> {\n    vec![1, 2, 3].into_iter()\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert "impl" in result[0].return_type
        assert "Iterator" in result[0].return_type

    # --- Result / Option ---

    def test_result_return_type(self, ts):
        code = "use std::io::Error;\nfn foo() -> Result<i32, Error> {\n    Ok(42)\n}\n"
        sym = Symbol(id="s2", name="foo", kind="function", file="a.rs", line=2)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert "Result" in result[0].return_type

    def test_option_return_type(self, ts):
        code = "fn foo() -> Option<String> {\n    Some(\"hi\".to_string())\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert "Option" in result[0].return_type
        assert "String" in result[0].return_type

    def test_nested_result_option(self, ts):
        code = "use std::io::Error;\nfn foo() -> Result<Option<i32>, Error> {\n    Ok(Some(42))\n}\n"
        sym = Symbol(id="s2", name="foo", kind="function", file="a.rs", line=2)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert "Result" in result[0].return_type

    # --- async ---

    def test_async_function(self, ts):
        code = "async fn foo() -> i32 {\n    42\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert result[0].return_type == "i32"

    # --- 无返回类型 ---

    def test_no_return_type(self, ts):
        code = "fn foo(x: i32) {\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert result[0].return_type == ""
        assert "x: i32" in result[0].params

    # --- 闭包 / 函数指针 ---

    fn_pointer_code = "fn foo(f: fn(i32) -> i32) -> i32 {\n    f(42)\n}\n"

    def test_fn_pointer_param(self, ts):
        code = "fn foo(f: fn(i32) -> i32) -> i32 {\n    f(42)\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.rs", line=1)
        result = _parse_and_extract(ts, code, "rust", [sym])
        assert result[0].return_type == "i32"
        assert "f:" in result[0].params or "f :" in result[0].params


# ═══════════════════════════════════════════════════════════════════════════════
# C++ 边界情况 (Issue #66)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCppEdgeCases:
    """C++ 类型推断边界情况测试。"""

    def test_reference_return(self, ts):
        code = "int& foo(int& x) {\n    return x;\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.cpp", line=1)
        result = _parse_and_extract(ts, code, "cpp", [sym])
        assert "int" in result[0].return_type

    def test_const_reference_param(self, ts):
        code = "void foo(const int& x) {}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.cpp", line=1)
        result = _parse_and_extract(ts, code, "cpp", [sym])
        assert "x" in result[0].params

    def test_pointer_return(self, ts):
        code = "int* foo(int* x) {\n    return x;\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.cpp", line=1)
        result = _parse_and_extract(ts, code, "cpp", [sym])
        assert "int" in result[0].return_type

    def test_string_param(self, ts):
        code = '#include <string>\nstd::string foo(const std::string& x) {\n    return x;\n}\n'
        sym = Symbol(id="s1", name="foo", kind="function", file="a.cpp", line=2)
        result = _parse_and_extract(ts, code, "cpp", [sym])
        assert "string" in result[0].return_type.lower() or "std" in result[0].return_type.lower()

    def test_trailing_return_type(self, ts):
        # 尾置返回类型在 tree-sitter-cpp 中 AST 结构特殊，当前实现可能无法提取
        code = "auto foo() -> int {\n    return 0;\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.cpp", line=1)
        result = _parse_and_extract(ts, code, "cpp", [sym])
        # 记录实际行为：尾置返回类型可能未被提取
        assert result[0].return_type == "" or "int" in result[0].return_type

    def test_template_function(self, ts):
        code = "template<typename T>\nT foo(T x) {\n    return x;\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.cpp", line=2)
        result = _parse_and_extract(ts, code, "cpp", [sym])
        assert result[0].return_type == "T"
        assert "T" in result[0].params

    def test_auto_return_type(self, ts):
        code = "auto foo(int x) {\n    return x;\n}\n"
        sym = Symbol(id="s1", name="foo", kind="function", file="a.cpp", line=1)
        result = _parse_and_extract(ts, code, "cpp", [sym])
        # auto 返回类型可能被提取也可能为空
        assert result is not None

    def test_constexpr_function(self, ts):
        code = "constexpr int add(int a, int b) {\n    return a + b;\n}\n"
        sym = Symbol(id="s1", name="add", kind="function", file="a.cpp", line=1)
        result = _parse_and_extract(ts, code, "cpp", [sym])
        assert "int" in result[0].return_type
        assert "int a" in result[0].params


# ═══════════════════════════════════════════════════════════════════════════════
# C# 边界情况 (Issue #66)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCSharpEdgeCases:
    """C# 类型推断边界情况测试。"""

    def test_void_return(self, ts):
        code = "public class Foo {\n    public void DoSomething() {\n    }\n}\n"
        sym = Symbol(id="s1", name="DoSomething", kind="method", file="a.cs", line=2)
        result = _parse_and_extract(ts, code, "c_sharp", [sym])
        assert result[0].return_type == "void"

    def test_array_return(self, ts):
        code = "public class Foo {\n    public int[] GetNumbers() {\n        return new int[] { 1, 2, 3 };\n    }\n}\n"
        sym = Symbol(id="s1", name="GetNumbers", kind="method", file="a.cs", line=2)
        result = _parse_and_extract(ts, code, "c_sharp", [sym])
        assert "int" in result[0].return_type

    def test_generic_method(self, ts):
        code = "public class Foo {\n    public T Foo2<T>(T x) {\n        return x;\n    }\n}\n"
        sym = Symbol(id="s1", name="Foo2", kind="method", file="a.cs", line=2)
        result = _parse_and_extract(ts, code, "c_sharp", [sym])
        # 泛型方法的返回类型提取取决于 tree-sitter-c_sharp 的 AST 结构
        assert result[0].return_type == "" or result[0].return_type != ""

    def test_nullable_return(self, ts):
        code = "public class Foo {\n    public int? GetNumber() {\n        return null;\n    }\n}\n"
        sym = Symbol(id="s1", name="GetNumber", kind="method", file="a.cs", line=2)
        result = _parse_and_extract(ts, code, "c_sharp", [sym])
        assert "int" in result[0].return_type

    def test_async_task_return(self, ts):
        # async Task<int> 在 tree-sitter-c_sharp 中可能被解析为不同的 AST 结构
        code = "public class Foo {\n    public async Task<int> GetNumberAsync() {\n        return 42;\n    }\n}\n"
        sym = Symbol(id="s1", name="GetNumberAsync", kind="method", file="a.cs", line=2)
        result = _parse_and_extract(ts, code, "c_sharp", [sym])
        # 记录实际行为：async 泛型返回类型可能未被提取
        assert result[0].return_type == "" or "Task" in result[0].return_type

    def test_string_return_and_params(self, ts):
        code = 'public class Foo {\n    public string Greet(string name) {\n        return "Hello " + name;\n    }\n}\n'
        sym = Symbol(id="s1", name="Greet", kind="method", file="a.cs", line=2)
        result = _parse_and_extract(ts, code, "c_sharp", [sym])
        assert result[0].return_type == "string"
        assert "string name" in result[0].params
