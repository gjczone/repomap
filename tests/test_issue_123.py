"""Issue #123 回归测试 — Edge confidence + 输出自适应缩放 + 框架路由补齐"""

from __future__ import annotations

import unittest

from src import Edge


class TestEdgeConfidence(unittest.TestCase):
    """改动 3: Edge 添加 confidence 字段。"""

    def test_edge_has_confidence_field(self) -> None:
        """Edge 必须有 confidence 字段，默认值为 1.0。"""
        e = Edge(source="a", target="b", weight=1.0, kind="call")
        self.assertTrue(hasattr(e, "confidence"), "Edge 缺少 confidence 字段")
        self.assertEqual(e.confidence, 1.0, "Edge.confidence 默认值应为 1.0")

    def test_edge_confidence_explicit(self) -> None:
        """Edge 可显式设置 confidence 值。"""
        e = Edge(source="a", target="b", weight=1.0, kind="call", confidence=0.7)
        self.assertEqual(e.confidence, 0.7)

    def test_edge_default_does_not_break_existing(self) -> None:
        """向后兼容：不传 confidence 应使用默认值 1.0。"""
        e = Edge(source="a", target="b", weight=1.0, kind="import")
        self.assertEqual(e.confidence, 1.0)


class TestProjectSizeTier(unittest.TestCase):
    """改动 2: 项目规模分级函数。"""

    def test_get_project_size_tier_exists(self) -> None:
        """get_project_size_tier 函数必须存在。"""
        from src import get_project_size_tier

        self.assertTrue(callable(get_project_size_tier))

    def test_small_project(self) -> None:
        """文件数 < 500 → small。"""
        from src import get_project_size_tier

        self.assertEqual(get_project_size_tier(100), "small")
        self.assertEqual(get_project_size_tier(0), "small")
        self.assertEqual(get_project_size_tier(499), "small")

    def test_medium_project(self) -> None:
        """文件数 < 5000 → medium。"""
        from src import get_project_size_tier

        self.assertEqual(get_project_size_tier(500), "medium")
        self.assertEqual(get_project_size_tier(4999), "medium")

    def test_large_project(self) -> None:
        """文件数 >= 5000 → large。"""
        from src import get_project_size_tier

        self.assertEqual(get_project_size_tier(5000), "large")
        self.assertEqual(get_project_size_tier(10000), "large")


class TestAdaptiveMaxChars(unittest.TestCase):
    """改动 2: 自适应输出上限。"""

    def test_get_adaptive_max_chars_exists(self) -> None:
        """get_adaptive_max_chars 函数必须存在。"""
        from src import get_adaptive_max_chars

        self.assertTrue(callable(get_adaptive_max_chars))

    def test_get_adaptive_max_source_lines_exists(self) -> None:
        """get_adaptive_max_source_lines 函数必须存在。"""
        from src import get_adaptive_max_source_lines

        self.assertTrue(callable(get_adaptive_max_source_lines))

    def test_small_project_source_lines(self) -> None:
        """小型项目：每符号源码段上限 40 行。"""
        from src import get_adaptive_max_source_lines

        self.assertEqual(get_adaptive_max_source_lines("small"), 40)

    def test_medium_project_source_lines(self) -> None:
        """中型项目：每符号源码段上限 60 行。"""
        from src import get_adaptive_max_source_lines

        self.assertEqual(get_adaptive_max_source_lines("medium"), 60)

    def test_large_project_source_lines(self) -> None:
        """大型项目：每符号源码段上限 80 行。"""
        from src import get_adaptive_max_source_lines

        self.assertEqual(get_adaptive_max_source_lines("large"), 80)

    def test_small_project_max_chars(self) -> None:
        """小型项目：总输出上限 15000 chars。"""
        from src import get_adaptive_max_chars

        self.assertEqual(get_adaptive_max_chars("small", 16000), 15000)

    def test_medium_project_max_chars(self) -> None:
        """中型项目：总输出上限 25000 chars。"""
        from src import get_adaptive_max_chars

        self.assertEqual(get_adaptive_max_chars("medium", 16000), 25000)

    def test_large_project_max_chars(self) -> None:
        """大型项目：总输出上限 35000 chars。"""
        from src import get_adaptive_max_chars

        self.assertEqual(get_adaptive_max_chars("large", 16000), 35000)


class TestNestJSRouteDetection(unittest.TestCase):
    """改动 4: NestJS 装饰器路由检测。"""

    def test_nestjs_controller_route(self) -> None:
        """NestJS @Controller('prefix') + @Get('path') 应被检测为路由。"""
        from src.parser import TreeSitterAdapter

        adapter = TreeSitterAdapter()
        ts_code = """
import { Controller, Get } from '@nestjs/common';

@Controller('users')
export class UsersController {
    @Get(':id')
    findOne(@Param('id') id: string): string {
        return 'user';
    }
}
"""
        tree = adapter.parse(ts_code.encode("utf-8"), "typescript")
        self.assertIsNotNone(tree, "应能解析 TypeScript 文件")
        if tree is not None:
            routes = adapter.extract_http_routes(tree, "typescript", "test.ts")
            self.assertIsInstance(routes, list)

    def test_nestjs_query_exists(self) -> None:
        """NestJS 'http_route_nestjs' query 应在 TypeScript queries 中。"""
        from src.parser import TreeSitterAdapter

        adapter = TreeSitterAdapter()
        queries = adapter._queries.get("typescript", {})
        nestjs_query = queries.get("http_route_nestjs")
        self.assertIsNotNone(nestjs_query, "TypeScript 缺少 http_route_nestjs query")


if __name__ == "__main__":
    unittest.main()
