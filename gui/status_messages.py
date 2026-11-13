"""Status bar that shows its messages in the display language.

Status messages are written in English all over the window code.  Rather than
touching every call site, the bar translates each message as it is shown:
fixed sentences through the shared catalog, and sentences with numbers or
file names through the patterns below.
"""
from __future__ import annotations

import re
from typing import Callable

from PySide6.QtWidgets import QStatusBar

from utils.i18n import LANG_ZH_CN, tr

# (pattern, Chinese template) for messages built with f-strings.
_ZH_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^Loaded: (?P<name>.+) — viewer showing (?P<count>\d+) images\. Select one image to draw ROIs\.$"),
     "已载入 {name}。当前显示 {count} 张图像，请先勾选其中一张再框选。"),
    (re.compile(r"^Loaded: (?P<name>.+) — draw a ROI to continue\.$"),
     "已载入 {name}。请在图像上框选泳道。"),
    (re.compile(r"^Loading (?P<name>.+) — preparing preview…$"),
     "正在载入 {name}，准备预览…"),
    (re.compile(r"^Preview ready: (?P<name>.+) — loading full-resolution quantitative data…$"),
     "预览已就绪：{name}，正在载入完整分辨率的定量数据…"),
    (re.compile(r"^Lane ROI set \((?P<w>\d+) × (?P<h>\d+) px\) — now draw a band ROI to continue\.$"),
     "泳道已框选（{w} × {h} 像素）。接下来请框选条带。"),
    (re.compile(r"^Band ROI set \((?P<w>\d+) × (?P<h>\d+) px\) — click Analyze to measure\.$"),
     "条带已框选（{w} × {h} 像素）。点击“定量分析”或按回车开始测量。"),
    (re.compile(r"^WB Plot ROI set \((?P<w>\d+) × (?P<h>\d+) px\) — select a blot target and press Return/Enter\.$"),
     "区域已框选（{w} × {h} 像素）。请选中一个印迹图框，然后按回车。"),
    (re.compile(r"^Uploading (?P<count>\d+) file\(s\) — converting Image Lab documents…$"),
     "正在导入 {count} 个文件，转换 Image Lab 文档中…"),
    (re.compile(r"^Uploading — (?P<message>.+)$"), "正在导入：{message}"),
    (re.compile(r"^Upload complete — opened (?P<opened>\d+) new image\(s\); (?P<ready>\d+) selected TIFF\(s\) are ready\.$"),
     "导入完成：新打开 {opened} 张图像，共 {ready} 张可用。"),
    (re.compile(r"^Upload complete\. (?P<count>\d+) TIFF\(s\) are ready in Uploaded Files, but all (?P<panels>\d+) image windows are full\.$"),
     "导入完成。{count} 张图像已在左侧文件列表中，但 {panels} 个图像窗口都已占用。"),
    (re.compile(r"^Uploaded (?P<count>\d+) file\(s\)\. TIFF images are ready in the left file list\.$"),
     "已导入 {count} 个文件，可在左侧文件列表中打开。"),
    (re.compile(r"^Removed (?P<name>.+) from Uploaded Files\.$"), "已从文件列表移除 {name}。"),
    (re.compile(r"^Autofilled (?P<count>\d+) value\(s\) into Replicate (?P<rep>\d+) — (?P<kind>.+)\.$"),
     "已把 {count} 个数值填入“重复 {rep} — {kind}”。"),
    (re.compile(r"^Image Transform applied: Low (?P<low>\S+), High (?P<high>\S+), Gamma (?P<gamma>\S+)\.$"),
     "已调整对比度：低 {low}，高 {high}，Gamma {gamma}。"),
    (re.compile(r"^Auto Scale applied: Low (?P<low>\S+), High (?P<high>\S+), Gamma (?P<gamma>\S+)\.$"),
     "已自动调整对比度：低 {low}，高 {high}，Gamma {gamma}。"),
    (re.compile(r"^Fixed ROI size selected for (?P<count>\d+) WB image window\(s\)\. Click/drag to place it\.$"),
     "已为 {count} 个图像窗口选定固定 ROI 尺寸。点击或拖动来放置它。"),
    (re.compile(r"^Edited WB Plot Auto-Fit ROIs — (?P<count>\d+) band ROI\(s\) currently selected\.$"),
     "已修改自动贴合的区域，当前选中 {count} 个条带。"),
    (re.compile(r"^Band (?P<band>\S+) detected — review overlay, then click Analyze\.$"),
     "已识别条带 {band}。请检查标记，然后点击“定量分析”。"),
]

_ZH_KINDS = {
    "Target band": "目的蛋白条带",
    "Loading control": "内参条带",
    "Normalized result": "归一化结果",
}


def localize_status_message(text: str, language: str) -> str:
    """Translate one status message; unknown text is returned unchanged."""
    if language != LANG_ZH_CN or not text:
        return text
    translated = tr(text, language)
    if translated != text:
        return translated
    for pattern, template in _ZH_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        values = match.groupdict()
        if "kind" in values:
            values["kind"] = _ZH_KINDS.get(values["kind"], values["kind"])
        return template.format(**values)
    return text


class LocalizedStatusBar(QStatusBar):
    """QStatusBar whose messages follow the window's display language."""

    def __init__(self, language_provider: Callable[[], str], parent=None) -> None:
        super().__init__(parent)
        self._language_provider = language_provider

    def showMessage(self, text: str, timeout: int = 0) -> None:  # type: ignore[override]
        try:
            language = self._language_provider()
        except Exception:
            language = ""
        super().showMessage(localize_status_message(text, language), timeout)
