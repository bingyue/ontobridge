"""Official MCP SDK stdio transport; business authorization stays in the API."""

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("OntoBridge")


def call(name: str, arguments: dict) -> dict[str, Any]:
    url = os.environ.get("ONTOBRIDGE_API_URL", "http://127.0.0.1:8787").rstrip("/")
    token = os.environ.get("ONTOBRIDGE_API_TOKEN")
    if not token:
        raise ValueError("需要配置 ONTOBRIDGE_API_TOKEN；使用只读或 operator 专用令牌")
    with httpx.Client(timeout=30) as client:
        response = client.post(
            f"{url}/api/capabilities/{name}/call",
            json=arguments,
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.is_error:
        raise ValueError(
            f"业务服务拒绝请求 ({response.status_code}): {response.json().get('detail', '调用失败')}"
        )
    return response.json()


@mcp.tool()
def list_due_orders(days: int = 7) -> dict[str, Any]:
    """查询未来 days 天（含逾期）订单及规则风险，附来源与时效信息。"""
    return call("list_due_orders", {"days": days})


@mcp.tool()
def get_order_360(order_id: str) -> dict[str, Any]:
    """获取订单、工单、工序、物料及来源；禁止把 unknown 当作无风险。"""
    return call("get_order_360", {"order_id": order_id})


@mcp.tool()
def check_delivery_risk(order_id: str) -> dict[str, Any]:
    """执行 delivery_risk_v1 确定性估算，结果是推断，不是已执行事实。"""
    return call("check_delivery_risk", {"order_id": order_id})


@mcp.tool()
def get_material_shortage(order_id: str) -> dict[str, Any]:
    """按交期分配共享库存，返回剩余生产需求及物料缺口。"""
    return call("get_material_shortage", {"order_id": order_id})


@mcp.tool()
def get_production_progress(order_id: str) -> dict[str, Any]:
    """汇总 MES 工单累计完工量、分配数量及剩余工序。"""
    return call("get_production_progress", {"order_id": order_id})


@mcp.tool()
def simulate_schedule(extra_hours: float = 2) -> dict[str, Any]:
    """以交期优先启发式比较每天追加工时；不保证最优，不提交排程。"""
    return call("simulate_schedule", {"extra_hours": extra_hours})


@mcp.tool()
def propose_schedule_change(
    order_id: str, note: str, idempotency_key: str, extra_hours: float = 2
) -> dict[str, Any]:
    """创建待人工审批的排产草案，需要 operator 权限。不会写回 MES。"""
    return call(
        "propose_schedule_change",
        {"order_id": order_id, "note": note, "idempotency_key": idempotency_key, "extra_hours": extra_hours},
    )


def main():
    mcp.run(transport="stdio")
