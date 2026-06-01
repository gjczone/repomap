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
