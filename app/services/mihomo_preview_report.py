"""Human-readable presentation of the fixed, value-free offline preview contract.

The renderer accepts reports, never YAML. It validates the whole v1 shape first
and only emits fixed labels, safe counts and positions, not caller text.
"""
from __future__ import annotations

from app.services.mihomo_preview import validate_preview_report

_CHANGES = {
    "not_compared": "未提供基线，未比较", "added": "新增", "removed": "移除",
    "changed": "结构有变化", "same": "结构相同", "absent": "两份均无此区块",
}
# Every message explains this tool's narrow scope, not a runtime verdict.
_DIAGNOSTICS = {
    "MODE_NOT_RECOGNIZED": "模式不在本工具识别范围；仅识别 rule / global / direct 的规范拼写。",
    "NAME_REQUIRED": "名称缺失或不符合本工具的字面名称限制；检查非空字符串及 128 字符上限。",
    "NAME_COLLISION": "名称重复或与 DIRECT / REJECT 冲突；检查节点与组的跨区块命名。",
    "NODE_PROFILE_NOT_EXPRESSIBLE": "现有严格上游模型无法表达此节点；不要通过删除未知字段强行导入。",
    "GROUP_SHAPE_NOT_SUPPORTED": "组形状未被解释；仅识别 type 为 select、字段限于 name / type / proxies 的组，名称和成员另行检查。",
    "GROUP_MEMBERS_INVALID": "组成员列表不符合限制；检查非空字符串成员以及 1–128 项范围。",
    "DUPLICATE_GROUP_MEMBER": "组内成员重复；检查列表，工具不会自动去重。",
    "REFERENCE_UNRESOLVED": "引用无法在唯一节点/组名或 DIRECT / REJECT 中解析；检查命名与引用，其他内建目标尚未识别。",
    "GROUP_REFERENCE_CYCLE": "受支持的 select 子图存在引用环；检查该组及其引用路径。",
    "RULE_AFTER_MATCH": "规则位于已识别的 MATCH 之后；检查顺序，工具不会自动重排。",
    "RULE_SHAPE_NOT_SUPPORTED": "规则形状未被解释；仅检查规范 DOMAIN / DOMAIN-SUFFIX / IP-CIDR / IP-CIDR6 / MATCH。",
    "DUPLICATE_RULE": "同一精确谓词与目标重复；工具不会自动合并。",
    "RULE_TARGET_CONFLICT": "同一精确谓词指向不同目标；检查预期优先顺序，工具不会替你选目标。",
    "SECTION_NOT_INTERPRETED": "此区块只做结构差异比较，未验证语义；不能据此判定可导入。",
    "OPAQUE_SECTIONS_WITHHELD": "其他区块名称和值已隐藏，且不参与差异比较；可见区块相同不代表整份配置相同。",
}


def render_preview_text(report: dict) -> str:
    """Return a Chinese plain-text report, or a fixed MihomoPreviewError.

    A zero diagnostic count is deliberately never rendered as 'compatible'.
    Positions are one-based list items alongside the unchanged zero-based JSON
    index; neither is a YAML source line number.
    """
    validate_preview_report(report)
    lines = [
        "Mihomo YAML 离线结构预览（只读）",
        "不可应用：未验证真实核心兼容性，未证明合并等价；不会写入配置或启动核心。",
        "识别数量不是可导入数量；诊断为零也不代表兼容。",
        "",
        "候选概况：",
        f"- 节点：{report['nodes']['count']} 项；现有上游模型可表达 {report['nodes']['existing_upstream_profile_expressible']} 项。",
        f"- 分组：{report['groups']['count']} 项；识别有限 select 形状 {report['groups']['recognized_select_shapes']} 项。",
        f"- 规则：{report['rules']['count']} 项；识别语法 {report['rules']['recognized_syntax']} 项（未验证重叠谓词与顺序等价）。",
        f"- 隐藏的其他区块：{report['withheld_section_count']} 个；名称、值及变化均不展示。",
        "",
        "区块结构差异（不是语义等价或服务端版本比较）：",
    ]
    for row in report["sections"] + report["uninterpreted_sections"]:
        state = "存在" if row["present"] else "不存在"
        note = "；语义未解释" if row in report["uninterpreted_sections"] else ""
        lines.append(f"- {row['section']}：候选{state}；{_CHANGES[row['change']]}{note}。")
    lines.extend(["可见区块相同不代表整份配置相同；隐藏区块未比较。", "", "诊断汇总（按事件计数，不是问题节点数）："])
    total = sum(report["diagnostic_counts"].values())
    lines.append(f"共 {total} 条；展示 {len(report['diagnostics'])} 条；省略 {report['diagnostics_omitted']} 条。")
    if not total:
        lines.append("在本工具的有限检查范围内未发现诊断；未检查的语义与真实运行仍需单独验证。")
    for code, count in sorted(report["diagnostic_counts"].items()):
        lines.append(f"- {code}：{count} 条。{_DIAGNOSTICS[code]}")
    if report["diagnostics"]:
        lines.extend(["", "诊断位置（列表项从 1 开始；括号中为 JSON 的零基索引，不是 YAML 行号）："])
        for row in report["diagnostics"]:
            position = f"第 {row['index'] + 1} 项（索引 {row['index']}）" if "index" in row else "区块"
            lines.append(f"- {row['section']} / {position}：{row['code']}。")
    return "\n".join(lines) + "\n"
