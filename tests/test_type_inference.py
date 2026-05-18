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
    assert tree is not None, f"无法用 {lang} parser 解析代码"
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
        code = (
            "class Foo:\n"
            "    def bar(self, x: int) -> str:\n"
            "        return str(x)\n"
        )
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
        code = (
            "class Foo {\n"
            "  method(x: number, y: string): void {}\n"
            "}\n"
        )
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
        code = (
            "public class Foo {\n"
            "    public void doSomething() {\n"
            "    }\n"
            "}\n"
        )
        sym = Symbol(id="s1", name="doSomething", kind="method", file="a.java", line=2)
        result = _parse_and_extract(ts, code, "java", [sym])
        assert result[0].return_type == "void"

    def test_constructor_no_return(self, ts):
        code = (
            "public class Foo {\n"
            "    public Foo(int x) {\n"
            "    }\n"
            "}\n"
        )
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
            id="s1", name="foo", kind="function", file="a.py", line=1,
            return_type="existing", params="existing"
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
