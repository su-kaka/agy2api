# AGENTS.md

本仓库使用 `uv` 管理依赖(`pyproject.toml` / `uv.lock`)。

## Python 环境(重要)

**在执行任何 Python 相关命令(运行、测试、安装依赖等)时,请始终使用 backend 目录下的虚拟环境解释器:**

```bash
.venv\Scripts\python.exe
```

例如:

```bash
# 运行后端
.venv\Scripts\python.exe web.py

# 安装依赖(优先使用 uv)
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 查看已安装的包
.venv\Scripts\python.exe -m pip list
```

不要使用系统全局的 `python` / `python3`,也不要自行新建虚拟环境。

## 前端构建

前端位于 `frontend/` 目录,使用 `pnpm` 作为包管理器:

```bash
cd frontend
pnpm install
pnpm build
```

不要使用 `npm` / `yarn` 或创建 `package-lock.json` / `yarn.lock`。

## 依赖导入规范

**所有依赖导入(`import` / `from ... import ...`)必须统一放在文件顶部(module docstring、`__future__` 导入和 `__all__` 之后)。**

唯一的例外:**存在循环依赖时**,可以在函数/方法内部延迟导入,并在导入语句旁注释说明原因,例如:

```python
def some_func():
    # 循环依赖,延迟导入
    from src.auth import verify_token
```

除上述例外,禁止在函数内部、条件分支中随意散落导入语句。
