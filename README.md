# kf-tool

个人工具集合。每个子目录是一个**独立可用**的工具或程序，自带 README、依赖清单和许可证，不依赖同仓库的其它目录。

| 工具 | 一句话 | 技术栈 | 入口 | 许可 |
| --- | --- | --- | --- | --- |
| [DocToolkit](DocToolkit/) | 中文公文排版 + 图片 / PDF 转 Word 初稿 | Python 3.9+ | `gongwen.bat` / `ocr2docx.bat` / `add_copyright.bat` | AGPL-3.0 |

## 使用

```bash
git clone https://github.com/fengyale/kf-tool.git
cd kf-tool/DocToolkit
pip install -r requirements.txt
```

Windows 上把 txt、图片文件夹、PDF 或 docx 直接拖到对应的 `.bat` 上即可，详见各子项目的 README。

## 什么该进来

- 进：自用或对外分发的工具、脚本，一个目录一个，有自己的入口和 README
- 不进：技能（放 [kf-skill](https://github.com/fengyale/kf-skill)）；有独立发布节奏、需要自己的 CI / issue / 版本号的重型项目，留独立仓库

## 许可

本仓库**不设统一许可证**，每个子项目各自带 `LICENSE`：

| 子项目 | 许可 | 原因 |
| --- | --- | --- |
| DocToolkit | AGPL-3.0 | 依赖 PyMuPDF（AGPL-3.0 / 商业双授权），分发组合作品须以 AGPL 发布 |
