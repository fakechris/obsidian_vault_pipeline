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
# 运行测试
pytest -q

# 预览模式测试 Pipeline
ovp --full --dry-run

# 运行一致性检查
ovp-lint --check
```

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
