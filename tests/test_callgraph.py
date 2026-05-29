from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest

from src.callgraph import (
    ClassInfo,
    ModuleInfo,
    _PyCallGraphVisitor,
    _walk_go_node,
    _walk_rust_node,
    _walk_ts_node,
    analyze_python_callgraph,
    resolve_precise_edges,
)
from src.parser import TreeSitterAdapter


@pytest.fixture(scope="module")
def ts_adapter():
    return TreeSitterAdapter()


# ═══════════════════════════════════════════════════════════════════════════════
# Python 调用图测试（基于 ast 模块）
# ═══════════════════════════════════════════════════════════════════════════════


def _parse_py(source: str) -> _PyCallGraphVisitor:
    tree = ast.parse(source)
    visitor = _PyCallGraphVisitor("test.py")
    visitor.visit(tree)
    return visitor


class TestPythonCallgraph:
    def test_simple_function_call(self):
        source = """\
def bar():
    pass

def foo():
    bar()
"""
        v = _parse_py(source)
        assert "bar" in v.info.functions
        assert "foo" in v.info.functions
        call_names = [c[0] for c in v.info.calls]
        assert "bar" in call_names

    def test_self_method_resolution(self):
        source = """\
class MyClass:
    def helper(self):
        pass

    def do_work(self):
        self.helper()
"""
        v = _parse_py(source)
        assert "MyClass" in v.info.classes
        assert "helper" in v.info.classes["MyClass"].methods
        assert "do_work" in v.info.classes["MyClass"].methods
        call_names_with_class = [(c[0], c[1]) for c in v.info.calls]
        assert ("self.helper", "MyClass") in call_names_with_class

    def test_cls_method_resolution(self):
        source = """\
class MyClass:
    @classmethod
    def create(cls):
        cls.helper()

    @classmethod
    def helper(cls):
        pass
"""
        v = _parse_py(source)
        assert "MyClass" in v.info.classes
        call_names_with_class = [(c[0], c[1]) for c in v.info.calls]
        assert ("cls.helper", "MyClass") in call_names_with_class

    def test_cross_file_import_resolution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "module_a.py").write_text(
                "from module_b import helper\n\ndef caller():\n    helper()\n"
            )
            (root / "module_b.py").write_text("def helper():\n    pass\n")
            modules = analyze_python_callgraph(root, ["module_a.py", "module_b.py"])
            assert "module_a.py" in modules
            assert "module_b.py" in modules
            a_info = modules["module_a.py"]
            assert "helper" in a_info.imports
            assert a_info.imports["helper"] == "module_b.helper"
            call_names = [c[0] for c in a_info.calls]
            assert "helper" in call_names

    def test_module_level_calls(self):
        source = """\
import logging
logger = logging.getLogger(__name__)
logger.info("started")
"""
        v = _parse_py(source)
        call_names = [c[0] for c in v.info.calls]
        assert "logging.getLogger" in call_names
        assert "logger.info" in call_names

    def test_nested_function_call_attribution(self):
        """验证嵌套函数内调用的归属正确。

        场景：outer() 函数内定义了 inner() 函数，
        inner() 内部的调用应归属于 inner，而不是 outer。
        """
        source = """\
def outer():
    def inner():
        print("hello")
    inner()
"""
        v = _parse_py(source)
        # 验证嵌套函数被注册
        assert "outer" in v.info.functions
        assert "inner" in v.info.functions
        # 验证调用归属：inner 内的 print 调用应归属于 inner
        inner_calls = [c for c in v.info.calls if c[0] == "print"]
        assert len(inner_calls) == 1
        # 验证 inner() 调用归属于 outer
        outer_calls = [c for c in v.info.calls if c[0] == "inner"]
        assert len(outer_calls) == 1

    def test_nested_classes(self):
        source = """\
class Outer:
    def outer_method(self):
        pass

    class Inner:
        def inner_method(self):
            pass
"""
        v = _parse_py(source)
        assert "Outer" in v.info.classes
        assert "Inner" in v.info.classes
        assert "outer_method" in v.info.classes["Outer"].methods
        assert "inner_method" in v.info.classes["Inner"].methods

    def test_import_from(self):
        source = """\
from os.path import join
from collections import defaultdict as dd
"""
        v = _parse_py(source)
        assert "join" in v.info.imports
        assert v.info.imports["join"] == "os.path.join"
        assert "dd" in v.info.imports
        assert v.info.imports["dd"] == "collections.defaultdict"

    def test_class_method_not_in_functions(self):
        source = """\
class MyClass:
    def method_a(self):
        pass

def standalone():
    pass
"""
        v = _parse_py(source)
        assert "method_a" not in v.info.functions
        assert "standalone" in v.info.functions

    def test_async_function_call(self):
        source = """\
async def fetch():
    pass

async def handler():
    await fetch()
"""
        v = _parse_py(source)
        assert "fetch" in v.info.functions
        assert "handler" in v.info.functions
        call_names = [c[0] for c in v.info.calls]
        assert "fetch" in call_names

    def test_chained_attribute_call(self):
        source = """\
def foo():
    self.obj.method()
"""
        v = _parse_py(source)
        call_names = [c[0] for c in v.info.calls]
        assert "self.obj.method" in call_names


# ═══════════════════════════════════════════════════════════════════════════════
# TypeScript 调用图测试（基于 tree-sitter）
# ═══════════════════════════════════════════════════════════════════════════════


class TestTSCallgraph:
    def test_class_with_methods(self, ts_adapter):
        source = b"""\
class UserService {
    getUser() {}
    deleteUser() {}
}
"""
        tree = ts_adapter.parse(source, "typescript")
        info = ModuleInfo("test.ts")
        _walk_ts_node(tree.root_node, info, [None])
        assert "UserService" in info.classes
        assert "getUser" in info.classes["UserService"].methods
        assert "deleteUser" in info.classes["UserService"].methods

    def test_function_declaration(self, ts_adapter):
        source = b"""\
function greet(name: string): string {
    return "hello " + name;
}
"""
        tree = ts_adapter.parse(source, "typescript")
        info = ModuleInfo("test.ts")
        _walk_ts_node(tree.root_node, info, [None])
        assert "greet" in info.functions

    def test_arrow_function(self, ts_adapter):
        source = b"""\
const add = (a: number, b: number) => a + b;
"""
        tree = ts_adapter.parse(source, "typescript")
        info = ModuleInfo("test.ts")
        _walk_ts_node(tree.root_node, info, [None])
        assert "add" in info.functions

    def test_import_named(self, ts_adapter):
        source = b"""\
import { foo, bar } from 'my-module';
"""
        tree = ts_adapter.parse(source, "typescript")
        info = ModuleInfo("test.ts")
        _walk_ts_node(tree.root_node, info, [None])
        assert "foo" in info.imports
        assert "bar" in info.imports
        assert info.imports["foo"] == "my-module"
        assert info.imports["bar"] == "my-module"

    def test_import_default(self, ts_adapter):
        source = b"""\
import React from 'react';
"""
        tree = ts_adapter.parse(source, "typescript")
        info = ModuleInfo("test.ts")
        _walk_ts_node(tree.root_node, info, [None])
        assert "React" in info.imports
        assert info.imports["React"] == "react"

    def test_import_with_alias(self, ts_adapter):
        source = b"""\
import { foo as bar } from 'x';
"""
        tree = ts_adapter.parse(source, "typescript")
        info = ModuleInfo("test.ts")
        _walk_ts_node(tree.root_node, info, [None])
        assert "bar" in info.imports
        assert info.imports["bar"] == "x"

    def test_namespace_import(self, ts_adapter):
        source = b"""\
import * as ns from 'x';
"""
        tree = ts_adapter.parse(source, "typescript")
        info = ModuleInfo("test.ts")
        _walk_ts_node(tree.root_node, info, [None])
        assert "ns" in info.imports
        assert info.imports["ns"] == "x"

    def test_call_expression_simple(self, ts_adapter):
        source = b"""\
function main() {
    greet();
}
"""
        tree = ts_adapter.parse(source, "typescript")
        info = ModuleInfo("test.ts")
        _walk_ts_node(tree.root_node, info, [None])
        call_names = [c[0] for c in info.calls]
        assert "greet" in call_names

    def test_call_expression_member(self, ts_adapter):
        source = b"""\
function main() {
    obj.method();
}
"""
        tree = ts_adapter.parse(source, "typescript")
        info = ModuleInfo("test.ts")
        _walk_ts_node(tree.root_node, info, [None])
        call_names = [c[0] for c in info.calls]
        assert "obj.method" in call_names

    def test_method_call_inside_class(self, ts_adapter):
        source = b"""\
class Service {
    run() {
        this.process();
    }
    process() {}
}
"""
        tree = ts_adapter.parse(source, "typescript")
        info = ModuleInfo("test.ts")
        _walk_ts_node(tree.root_node, info, [None])
        calls_in_class = [(c[0], c[1]) for c in info.calls]
        assert ("this.process", "Service") in calls_in_class


# ═══════════════════════════════════════════════════════════════════════════════
# Go 调用图测试（基于 tree-sitter）
# ═══════════════════════════════════════════════════════════════════════════════


class TestGoCallgraph:
    def test_function_declaration(self, ts_adapter):
        source = b"""\
package main

func hello() {
}
"""
        tree = ts_adapter.parse(source, "go")
        info = ModuleInfo("main.go")
        _walk_go_node(tree.root_node, info, [None])
        assert "hello" in info.functions

    def test_method_declaration_with_receiver(self, ts_adapter):
        source = b"""\
package main

func (s *Server) Start() {
}
"""
        tree = ts_adapter.parse(source, "go")
        info = ModuleInfo("main.go")
        _walk_go_node(tree.root_node, info, [None])
        assert "Server" in info.classes
        assert "Start" in info.classes["Server"].methods

    def test_import_single(self, ts_adapter):
        source = b"""\
package main

import "fmt"
"""
        tree = ts_adapter.parse(source, "go")
        info = ModuleInfo("main.go")
        _walk_go_node(tree.root_node, info, [None])
        assert "fmt" in info.imports

    def test_import_grouped(self, ts_adapter):
        source = b"""\
package main

import (
    "fmt"
    "net/http"
)
"""
        tree = ts_adapter.parse(source, "go")
        info = ModuleInfo("main.go")
        _walk_go_node(tree.root_node, info, [None])
        assert "fmt" in info.imports
        assert "http" in info.imports

    def test_call_expression_simple(self, ts_adapter):
        source = b"""\
package main

func main() {
    hello()
}
"""
        tree = ts_adapter.parse(source, "go")
        info = ModuleInfo("main.go")
        _walk_go_node(tree.root_node, info, [None])
        call_names = [c[0] for c in info.calls]
        assert "hello" in call_names

    def test_call_expression_selector(self, ts_adapter):
        source = b"""\
package main

import "fmt"

func main() {
    fmt.Println("hello")
}
"""
        tree = ts_adapter.parse(source, "go")
        info = ModuleInfo("main.go")
        _walk_go_node(tree.root_node, info, [None])
        call_names = [c[0] for c in info.calls]
        assert "fmt.Println" in call_names

    def test_method_call_on_receiver(self, ts_adapter):
        source = b"""\
package main

func (s *Server) Run() {
    s.Start()
}

func (s *Server) Start() {
}
"""
        tree = ts_adapter.parse(source, "go")
        info = ModuleInfo("main.go")
        _walk_go_node(tree.root_node, info, [None])
        call_names = [c[0] for c in info.calls]
        assert "s.Start" in call_names

    def test_import_with_alias(self, ts_adapter):
        source = b"""\
package main

import f "fmt"
"""
        tree = ts_adapter.parse(source, "go")
        info = ModuleInfo("main.go")
        _walk_go_node(tree.root_node, info, [None])
        assert "f" in info.imports

    def test_method_followed_by_function(self, ts_adapter):
        """验证方法后紧跟独立函数时，函数调用不被错误关联到类方法。

        场景：Go 方法 Handle() 后紧跟独立函数 processRequest()，
        processRequest() 内部的调用应归属于 processRequest，
        而不是 Server.processRequest。
        """
        source = b"""\
package main

func (s *Server) Handle() {
    s.Start()
}

func processRequest() {
    fmt.Println("hello")
}
"""
        tree = ts_adapter.parse(source, "go")
        info = ModuleInfo("main.go")
        _walk_go_node(tree.root_node, info, [None])
        # 验证方法被正确记录
        assert "Server" in info.classes
        assert "Handle" in info.classes["Server"].methods
        # 验证独立函数被正确记录
        assert "processRequest" in info.functions
        # 验证调用归属：processRequest 内的调用不应有 receiver
        process_calls = [c for c in info.calls if c[0] == "fmt.Println"]
        assert len(process_calls) == 1
        assert process_calls[0][1] == ""  # receiver 应为空


# ═══════════════════════════════════════════════════════════════════════════════
# Rust 调用图测试（基于 tree-sitter）
# ═══════════════════════════════════════════════════════════════════════════════


class TestRustCallgraph:
    def test_function_item(self, ts_adapter):
        source = b"""\
fn main() {
}
"""
        tree = ts_adapter.parse(source, "rust")
        info = ModuleInfo("main.rs")
        _walk_rust_node(tree.root_node, info, [None])
        assert "main" in info.functions

    def test_impl_block(self, ts_adapter):
        source = b"""\
struct Server;

impl Server {
    fn start(&self) {}
    fn stop(&self) {}
}
"""
        tree = ts_adapter.parse(source, "rust")
        info = ModuleInfo("main.rs")
        _walk_rust_node(tree.root_node, info, [None])
        assert "Server" in info.classes
        assert "start" in info.classes["Server"].methods
        assert "stop" in info.classes["Server"].methods

    def test_impl_trait_for_type(self, ts_adapter):
        source = b"""\
struct MyType;

trait Handler {
    fn handle(&self);
}

impl Handler for MyType {
    fn handle(&self) {}
}
"""
        tree = ts_adapter.parse(source, "rust")
        info = ModuleInfo("main.rs")
        _walk_rust_node(tree.root_node, info, [None])
        assert "MyType" in info.classes
        assert "handle" in info.classes["MyType"].methods

    def test_use_declaration(self, ts_adapter):
        source = b"""\
use std::collections::HashMap;
"""
        tree = ts_adapter.parse(source, "rust")
        info = ModuleInfo("main.rs")
        _walk_rust_node(tree.root_node, info, [None])
        assert "HashMap" in info.imports

    def test_use_self_import(self, ts_adapter):
        """验证 Rust {self} 分组导入被正确记录。

        场景：use std::collections::{self, HashMap}，
        应该导入 collections 和 HashMap。
        """
        source = b"""\
use std::collections::{self, HashMap};
"""
        tree = ts_adapter.parse(source, "rust")
        info = ModuleInfo("main.rs")
        _walk_rust_node(tree.root_node, info, [None])
        # 验证 {self} 导入被正确记录
        assert "collections" in info.imports
        assert info.imports["collections"] == "std::collections"
        # 验证普通导入也被正确记录
        assert "HashMap" in info.imports
        assert info.imports["HashMap"] == "std::collections::HashMap"

    def test_call_expression_simple(self, ts_adapter):
        source = b"""\
fn main() {
    greet();
}
"""
        tree = ts_adapter.parse(source, "rust")
        info = ModuleInfo("main.rs")
        _walk_rust_node(tree.root_node, info, [None])
        call_names = [c[0] for c in info.calls]
        assert "greet" in call_names

    def test_call_expression_method(self, ts_adapter):
        source = b"""\
fn main() {
    obj.method();
}
"""
        tree = ts_adapter.parse(source, "rust")
        info = ModuleInfo("main.rs")
        _walk_rust_node(tree.root_node, info, [None])
        call_names = [c[0] for c in info.calls]
        assert "obj.method" in call_names

    def test_call_expression_scoped(self, ts_adapter):
        source = b"""\
fn main() {
    crate::module::func();
}
"""
        tree = ts_adapter.parse(source, "rust")
        info = ModuleInfo("main.rs")
        _walk_rust_node(tree.root_node, info, [None])
        call_names = [c[0] for c in info.calls]
        assert "crate::module::func" in call_names

    def test_self_method_call_in_impl(self, ts_adapter):
        source = b"""\
struct Worker;

impl Worker {
    fn run(&self) {
        Self::setup();
    }

    fn setup() {}
}
"""
        tree = ts_adapter.parse(source, "rust")
        info = ModuleInfo("main.rs")
        _walk_rust_node(tree.root_node, info, [None])
        calls_in_impl = [(c[0], c[1]) for c in info.calls]
        assert ("Self::setup", "Worker") in calls_in_impl

    def test_impl_method_not_in_functions(self, ts_adapter):
        source = b"""\
struct Foo;

impl Foo {
    fn bar(&self) {}
}

fn standalone() {}
"""
        tree = ts_adapter.parse(source, "rust")
        info = ModuleInfo("main.rs")
        _walk_rust_node(tree.root_node, info, [None])
        assert "bar" not in info.functions
        assert "standalone" in info.functions


# ═══════════════════════════════════════════════════════════════════════════════
# resolve_precise_edges 测试
# ═══════════════════════════════════════════════════════════════════════════════


class TestResolvePreciseEdges:
    def test_cross_file_resolution(self):
        modules = {}
        info_a = ModuleInfo("a.py")
        info_a.functions["caller"] = 1
        info_a.calls.append(("helper", "", 2))
        info_a.imports["helper"] = "b.helper"
        modules["a.py"] = info_a

        info_b = ModuleInfo("b.py")
        info_b.functions["helper"] = 5
        modules["b.py"] = info_b

        edges = resolve_precise_edges(modules)
        assert len(edges) > 0
        cross = [e for e in edges if e[0] == "a.py" and e[2] == "b.py"]
        assert len(cross) == 1
        assert cross[0][3] == 5
        assert cross[0][4] == "call"

    def test_method_call_resolution_self(self):
        modules = {}
        info = ModuleInfo("svc.py")
        cls = ClassInfo("Service")
        cls.methods["process"] = 10
        info.classes["Service"] = cls
        info.calls.append(("self.process", "Service", 5))
        modules["svc.py"] = info

        edges = resolve_precise_edges(modules)
        method_edges = [e for e in edges if e[4] == "method_call"]
        assert len(method_edges) == 1
        assert method_edges[0][2] == "svc.py"
        assert method_edges[0][3] == 10

    def test_method_call_resolution_cls(self):
        modules = {}
        info = ModuleInfo("svc.py")
        cls = ClassInfo("Service")
        cls.methods["create"] = 8
        info.classes["Service"] = cls
        info.calls.append(("cls.create", "Service", 3))
        modules["svc.py"] = info

        edges = resolve_precise_edges(modules)
        method_edges = [e for e in edges if e[4] == "method_call"]
        assert len(method_edges) == 1
        assert method_edges[0][3] == 8

    def test_import_call_resolution(self):
        modules = {}
        info_a = ModuleInfo("a.py")
        info_a.imports["utils"] = "pkg.utils"
        info_a.calls.append(("utils.format", "", 3))
        modules["a.py"] = info_a

        info_b = ModuleInfo("pkg/utils.py")
        info_b.functions["format"] = 12
        modules["pkg/utils.py"] = info_b

        edges = resolve_precise_edges(modules)
        import_edges = [e for e in edges if e[4] == "import_call"]
        assert len(import_edges) >= 1

    def test_class_method_direct_resolution(self):
        modules = {}
        info = ModuleInfo("svc.py")
        cls = ClassInfo("Service")
        cls.methods["start"] = 15
        info.classes["Service"] = cls
        info.calls.append(("Service.start", "", 5))
        modules["svc.py"] = info

        edges = resolve_precise_edges(modules)
        method_edges = [e for e in edges if e[4] == "method_call"]
        assert len(method_edges) == 1
        assert method_edges[0][3] == 15

    def test_no_resolution_for_unknown_call(self):
        modules = {}
        info = ModuleInfo("a.py")
        info.calls.append(("nonexistent", "", 1))
        modules["a.py"] = info

        edges = resolve_precise_edges(modules)
        assert len(edges) == 0

    def test_rust_scoped_call_with_import(self):
        modules = {}
        info_a = ModuleInfo("main.rs")
        info_a.imports["module"] = "crate::module"
        info_a.calls.append(("module::func", "", 3))
        modules["main.rs"] = info_a

        info_b = ModuleInfo("module.rs")
        info_b.functions["func"] = 7
        modules["module.rs"] = info_b

        edges = resolve_precise_edges(modules)
        import_edges = [e for e in edges if e[4] == "import_call"]
        assert len(import_edges) >= 1

    def test_same_file_function_call(self):
        modules = {}
        info = ModuleInfo("app.py")
        info.functions["main"] = 1
        info.functions["helper"] = 5
        info.calls.append(("helper", "", 2))
        modules["app.py"] = info

        edges = resolve_precise_edges(modules)
        assert len(edges) == 1
        assert edges[0][0] == "app.py"
        assert edges[0][2] == "app.py"
        assert edges[0][3] == 5
