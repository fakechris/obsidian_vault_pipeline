# Contribution Guide

欢迎贡献！

## 提交Issue

- 使用清晰的标题
- 描述问题和期望行为
- 提供复现步骤

## 提交PR

1. Fork 仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## 代码规范

- Python: PEP 8
- Shell: ShellCheck
- Markdown: markdownlint

## 测试

```bash
# 运行一致性检查
./60-Logs/scripts/check-consistency.sh

# 预览模式测试Pipeline
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full --dry-run
```

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
