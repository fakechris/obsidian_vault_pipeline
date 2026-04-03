# 推送到远程仓库指南

## 快速开始

```bash
# 1. 进入模板目录
cd /Users/chris/Documents/openclaw-template

# 2. 添加远程仓库（替换为你的仓库URL）
git remote add origin https://github.com/yourusername/para-zettelkasten-template.git

# 3. 推送到GitHub
git push -u origin main
```

## 创建GitHub仓库

### 方式1: GitHub CLI (推荐)

```bash
# 安装 gh (如果没有)
brew install gh  # macOS

# 登录
git gh auth login

# 创建仓库并推送
cd /Users/chris/Documents/openclaw-template
gh repo create para-zettelkasten-template --public --source=. --push
```

### 方式2: Web界面

1. 访问 https://github.com/new
2. Repository name: `para-zettelkasten-template`
3. 选择 Public
4. 不要初始化（README已存在）
5. 点击 Create repository
6. 复制推送命令:

```bash
cd /Users/chris/Documents/openclaw-template
git remote add origin https://github.com/yourusername/para-zettelkasten-template.git
git branch -M main
git push -u origin main
```

## 验证推送成功

```bash
# 检查远程
git remote -v

# 查看提交历史
git log --oneline -5

# 确认文件
ls -la
```

## 脱敏检查清单

推送前确保:

- [ ] `.env` 未提交（应被.gitignore排除）
- [ ] `.env.example` 已提交（模板文件）
- [ ] API keys 未出现在任何代码中
- [ ] 个人路径已移除（使用相对路径）
- [ ] 私有数据文件被.gitignore排除

## 自动化检查

```bash
# 搜索可能的敏感信息
grep -r "sk-" --include="*.py" --include="*.sh" --include="*.md" .
grep -r "token:" --include="*.py" --include="*.sh" .
grep -r "api_key" --include="*.py" --include="*.sh" .

# 应该只出现在.env.example中，其他位置不应该有
```

## 推送后设置

### 1. GitHub Actions配置

仓库 → Settings → Secrets and variables → Actions

添加以下Secrets:
- `PINBOARD_TOKEN` (可选)
- `AUTO_VAULT_API_KEY` (如果使用Actions自动化)

### 2. 主题和描述

在GitHub仓库页面:
- 添加描述: "PARA + Zettelkasten Knowledge Management System with Automation Pipeline"
- 添加主题标签: `obsidian`, `zettelkasten`, `para`, `knowledge-management`, `automation`
- 发布到 Releases

### 3. 启用Discussions

Settings → General → Discussions ✅

## 模板使用

其他人使用时:

```bash
# 克隆
git clone https://github.com/yourusername/para-zettelkasten-template.git my-vault

# 配置
cd my-vault
cp .env.example .env
# 编辑 .env 填入API keys

# 安装依赖
pip install -r requirements.txt

# 使用
python3 60-Logs/scripts/unified_pipeline_enhanced.py --full
```

## 维护更新

```bash
# 本地修改后
git add -A
git commit -m "feat: 描述修改"
git push

# 发布版本
git tag v1.0.1
git push origin v1.0.1
```

## 许可证确认

本模板使用 MIT License，允许:
- 商业使用
- 修改
- 分发
- 私人使用

唯一要求: 保留版权声明。
