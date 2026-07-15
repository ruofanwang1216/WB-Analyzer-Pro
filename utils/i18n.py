"""Small, explicit UI translation catalog for WB Analyzer Pro.

English source strings remain the stable internal keys.  This keeps analysis
data and exported column identifiers independent from the display language.
"""
from __future__ import annotations


LANG_EN = "en"
LANG_ZH_CN = "zh_CN"


_ZH_CN = {
    "English": "English",
    "中文": "中文",
    "Language": "语言",
    "Upload Files": "上传文件",
    "Image Transform": "图像显示调整",
    "Analyze": "定量分析",
    "Export All": "导出全部",
    "Reset All": "全部重置",
    "Densitometry Figure Generation": "灰度定量图生成",
    "Figure Generation": "灰度定量图生成",
    "Figures Generation": "生成图形",
    "Choose table type": "选择数据表类型",
    "Column": "列式表",
    "Grouped": "分组表",
    "Column Setup": "列式表设置",
    "Enter table dimensions": "设置数据表维度",
    "Samples: ": "样本组数：",
    "Replicates: ": "重复次数：",
    "Column Table": "列式数据表",
    "Select Negative Control": "选择阴性对照组",
    "Replicate": "重复",
    "Band Type": "条带类型",
    "Target band": "目的蛋白条带",
    "Loading control": "内参条带",
    "Normalized result": "归一化结果",
    "Replicate {number}": "重复 {number}",
    "Export Table": "导出数据表",
    "Export Figure": "导出图形",
    "Group {name}": "组 {name}",
    "Normalization baseline: {control} (mean Target/Loading = {baseline})": "归一化基线：{control}（目的蛋白／内参平均比值 = {baseline}）",
    "WB Plot Figure Generation": "Western blot 图像排版",
    "Uploaded Files": "已上传文件",
    "Export All TIFFs": "导出全部 TIFF 图像",
    "Figure Generation table will appear here.": "灰度定量图数据表将在此显示。",
    "Generated figure preview will appear here.": "生成的图形预览将在此显示。",
    "WB Plot Figure Generation will appear here.": "Western blot 图像排版工作区将在此显示。",
    "Layout": "图像布局",
    "Use a saved template": "使用已保存模板",
    "Normal WB": "常规 Western blot",
    "IP / Co-IP": "免疫共沉淀（IP / Co-IP）",
    "Dose-Response WB": "剂量反应 Western blot",
    "Multi-Panel (A / B / C)": "多版面（A / B / C）",
    "Apply Template": "应用模板",
    "Or create a new layout": "或新建图像布局",
    "Panels:": "版面数：",
    "Blots": "印迹图数：",
    "Lanes:": "泳道数：",
    "Apply Structure": "应用结构",
    "Add Blot Frame": "添加印迹图框",
    "Draw Band with ROI-Hit Enter": "绘制条带 ROI（按 Enter 应用）",
    "Selected target: none": "当前目标：未选择",
    "Selected target: added blot frame": "当前目标：新增印迹图框",
    "Selected target: {target}": "当前目标：{target}",
    "Fix This ROI": "固定此 ROI 尺寸",
    "Saved Blot Files": "已保存印迹图文件",
    "Open Blot File": "打开印迹图文件",
    "Export Figure": "导出图形",
    "Export PDF": "导出 PDF",
    "Export PPTX": "导出 PPTX",
    "+ Text": "+ 文本",
    "+ Line": "+ 线条",
    "Undo": "撤销",
    "Save Template": "保存模板",
    "Save Blot File": "保存印迹图文件",
    "Reset": "重置",
    "Font:": "字体：",
    "Size:": "字号：",
    "Rotate:": "旋转：",
    "Line:": "线宽：",
    "Same Size": "统一尺寸",
    "Match Largest": "匹配最大尺寸",
    "Match Smallest": "匹配最小尺寸",
    "Align text Boxes": "对齐文本框",
    "Align Left": "左对齐",
    "Align Center": "居中对齐",
    "Align Right": "右对齐",
    "Align Top": "顶部对齐",
    "Align Middle": "垂直居中",
    "Align Bottom": "底部对齐",
    "Distribute Horizontally": "水平分布",
    "Distribute Vertically": "垂直分布",
    " deg": " 度",
    " pt": " 磅",
    "Results": "定量结果",
    "Metric": "定量指标",
    "Run": "实验批次",
    "Band": "条带",
    "Lane": "泳道",
    "Area": "面积",
    "Mean": "平均灰度",
    "Min": "最小灰度",
    "Max": "最大灰度",
    "IntDen": "积分密度",
    "RawIntDen": "原始积分密度",
    "Delete": "删除",
    "Export Results": "导出结果",
    "Clear": "清除",
    "Manual Rotation": "手动旋转",
    "Custom Rotate": "自定义旋转",
    "Rotate": "应用旋转",
    "Cancel": "取消",
    "ROI Settings": "ROI 设置",
    "Lane Settings": "泳道设置",
    "Lanes:": "泳道数：",
    "Fixed ROI": "固定 ROI",
    "Fix ROI": "固定 ROI 尺寸",
    "Cancel fixed ROI": "取消固定 ROI",
    "Capture the current lane ROI size, or arm the next drawn ROI as the fixed size.": "记录当前泳道 ROI 尺寸；或将下一次绘制的 ROI 设为固定尺寸。",
    "Return Manual mode to freehand ROI drawing.": "恢复为手动自由绘制 ROI。",
    "Manual: draw lane ROI → draw band ROI → Analyze\nHold Space + drag to pan. Scroll to zoom.": (
        "手动模式：框选泳道 ROI → 框选条带 ROI → 定量分析\n"
        "按住空格键拖动可平移；滚动鼠标滚轮可缩放。"
    ),
    "Image Transform": "图像显示调整",
    "Analyze follows the current Low / High / Gamma. Invert remains preview-only.": (
        "定量分析将采用当前的低值／高值／伽马设置；反相仅影响预览显示。"
    ),
    "Invert display": "反相显示",
    "Show bright signal as dark bands on a light background": "在浅色背景上将高亮信号显示为深色条带",
    "Auto Scale": "自动缩放",
    "Close": "关闭",
    "Low": "低值",
    "High": "高值",
    "Gamma": "伽马",
    "Export All TIFFs": "导出全部 TIFF 图像",
    "No TIFF files to export.": "没有可导出的 TIFF 图像。",
    "Export Complete": "导出完成",
    "Exported {count} TIFF file(s) to:\n{directory}": "已导出 {count} 个 TIFF 图像至：\n{directory}",
    "Select Image": "选择图像",
    "Please select an image first.": "请先选择一张图像。",
    "No Image": "未选择图像",
    "Upload files first.": "请先上传文件。",
    "Custom Rotate-Hit Enter": "自定义旋转（按 Enter 应用）",
    "Flip Vertically": "垂直翻转",
    "Flip Horizontally": "水平翻转",
    "Undo Image Operation": "撤销图像操作",
    "Image Transform: Low / High / Gamma": "图像显示调整：低值／高值／伽马",
    "Rotate Image": "旋转图像",
    "Select this image for Auto Detect": "选择此图像用于自动检测",
    "Remove image": "移除图像",
    "Collapse Uploaded Files panel": "收起已上传文件面板",
    "Expand Uploaded Files panel": "展开已上传文件面板",
    "Reset. Upload files to begin.": "已重置。请上传文件开始分析。",
}


def tr(text: str, language: str = LANG_EN, **values: object) -> str:
    """Translate a UI string and substitute named values when supplied."""
    translated = _ZH_CN.get(text, text) if language == LANG_ZH_CN else text
    return translated.format(**values) if values else translated


def tr_display(text: str, language: str = LANG_EN, **values: object) -> str:
    """Translate text that may already be rendered in either supported language."""
    if language == LANG_ZH_CN:
        return tr(text, language, **values)
    english = {translated: source for source, translated in _ZH_CN.items()}
    source = english.get(text, text)
    return source.format(**values) if values else source
