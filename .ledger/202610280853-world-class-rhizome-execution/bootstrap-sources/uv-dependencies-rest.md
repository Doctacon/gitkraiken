# Managing dependencies | uv

](../config/#build-systems), it may declare dependencies that are required to build the project, but not required to run it. These dependencies are specified in the `[build-system]` table under `build-system.requires`, following [PEP 518](https://peps.python.org/pep-0518/).

For example, if a project uses `setuptools` as its build backend, it should declare `setuptools` as a build dependency:

pyproject.toml

`[](#__codelineno-58-1)[project] [](#__codelineno-58-2)name = "pandas" [](#__codelineno-58-3)version = "0.1.0" [](#__codelineno-58-4) [](#__codelineno-58-5)[build-system] [](#__codelineno-58-6)requires = ["setuptools>=42"] [](#__codelineno-58-7)build-backend = "setuptools.build_meta"`

By default, uv will respect `tool.uv.sources` when resolving build dependencies. For example, to use a local version of `setuptools` for building, add the source to `tool.uv.sources`:

pyproject.toml

`[](#__codelineno-59-1)[project] [](#__codelineno-59-2)name = "pandas" [](#__codelineno-59-3)version = "0.1.0" [](#__codelineno-59-4) [](#__codelineno-59-5)[build-system] [](#__codelineno-59-6)requires = ["setuptools>=42"] [](#__codelineno-59-7)build-backend = "setuptools.build_meta" [](#__codelineno-59-8) [](#__codelineno-59-9)[tool.uv.sources] [](#__codelineno-59-10)setuptools = { path = "./packages/setuptools" }`

When publishing a package, we recommend running `uv build --no-sources` to ensure that the package builds correctly when `tool.uv.sources` is disabled, as is the case when using other build tools, like [`pypa/build`](https://github.com/pypa/build).

## [Editable dependencies](#editable-dependencies)

A regular installation of a directory with a Python package first builds a wheel and then installs that wheel into your virtual environment, copying all source files. When the package source files are edited, the virtual environment will contain outdated versions.

Editable installations solve this problem by adding a link to the project within the virtual environment (a `.pth` file), which instructs the interpreter to include the source files directly.

There are some limitations to editables (mainly: the build backend needs to support them, and native modules aren't recompiled before import), but they are useful for development, as the virtual environment will always use the latest changes to the package.

uv uses editable installation for workspace packages by default.

To add an editable dependency, use the `--editable` flag:

`[](#__codelineno-60-1)$ uv add --editable ./path/foo`

Or, to opt-out of using an editable dependency in a workspace:

`[](#__codelineno-61-1)$ uv add --no-editable ./path/foo`

## [Virtual dependencies](#virtual-dependencies)

uv allows dependencies to be "virtual", in which the dependency itself is not installed as a [package](../config/#project-packaging), but its dependencies are.

By default, dependencies are never virtual.

A dependency with a [`path` source](#path) can be virtual if it explicitly sets [`tool.uv.package = false`](../../../reference/settings/#package). Without this setting, uv treats the path dependency as a normal package and will attempt to build it, even if the project does not declare a [build system](../config/#build-systems).

To treat a dependency as virtual, set `package = false` on the source:

pyproject.toml

`[](#__codelineno-62-1)[project] [](#__codelineno-62-2)dependencies = ["bar"] [](#__codelineno-62-3) [](#__codelineno-62-4)[tool.uv.sources] [](#__codelineno-62-5)bar = { path = "../projects/bar", package = false }`

If a dependency sets `tool.uv.package = false`, it can be overridden by declaring `package = true` on the source:

pyproject.toml

`[](#__codelineno-63-1)[project] [](#__codelineno-63-2)dependencies = ["bar"] [](#__codelineno-63-3) [](#__codelineno-63-4)[tool.uv.sources] [](#__codelineno-63-5)bar = { path = "../projects/bar", package = true }`

Similarly, a dependency with a [`workspace` source](#workspace-member) can be virtual if it explicitly sets [`tool.uv.package = false`](../../../reference/settings/#package). Without this setting, the workspace member will be built even if a [build system](../config/#build-systems) is not declared.

Workspace members that are _not_ dependencies can be virtual by default, e.g., if the parent `pyproject.toml` is:

pyproject.toml

`[](#__codelineno-64-1)[project] [](#__codelineno-64-2)name = "parent" [](#__codelineno-64-3)version = "1.0.0" [](#__codelineno-64-4)dependencies = [] [](#__codelineno-64-5) [](#__codelineno-64-6)[tool.uv.workspace] [](#__codelineno-64-7)members = ["child"]`

And the child `pyproject.toml` excluded a build system:

pyproject.toml

`[](#__codelineno-65-1)[project] [](#__codelineno-65-2)name = "child" [](#__codelineno-65-3)version = "1.0.0" [](#__codelineno-65-4)dependencies = ["anyio"]`

Then the `child` workspace member would not be installed, but the transitive dependency `anyio` would be.

In contrast, if the parent declared a dependency on `child`:

pyproject.toml

`[](#__codelineno-66-1)[project] [](#__codelineno-66-2)name = "parent" [](#__codelineno-66-3)version = "1.0.0" [](#__codelineno-66-4)dependencies = ["child"] [](#__codelineno-66-5) [](#__codelineno-66-6)[tool.uv.sources] [](#__codelineno-66-7)child = { workspace = true } [](#__codelineno-66-8) [](#__codelineno-66-9)[tool.uv.workspace] [](#__codelineno-66-10)members = ["child"]`

Then `child` would be built and installed.

## [Dependency specifiers](#dependency-specifiers)

uv uses standard [dependency specifiers](https://packaging.python.org/en/latest/specifications/dependency-specifiers/), originally defined in [PEP 508](https://peps.python.org/pep-0508/). A dependency specifier is composed of, in order:

*   The dependency name
*   The extras you want (optional)
*   The version specifier
*   An environment marker (optional)

The version specifiers are comma separated and added together, e.g., `foo >=1.2.3,<2,!=1.4.0` is interpreted as "a version of `foo` that's at least 1.2.3, but less than 2, and not 1.4.0".

Specifiers are padded with trailing zeros if required, so `foo ==2` matches foo 2.0.0, too.

A star can be used for the last digit with equals, e.g., `foo ==2.1.*` will accept any release from the 2.1 series. Similarly, `~=` matches where the last digit is equal or higher, e.g., `foo ~=1.2` is equal to `foo >=1.2,<2`, and `foo ~=1.2.3` is equal to `foo >=1.2.3,<1.3`.

Extras are comma-separated in square brackets between the name and version, e.g., `pandas[excel,plot] ==2.2`. Whitespace between extra names is ignored.

Some dependencies are only required in specific environments, e.g., a specific Python version or operating system. For example to install the `importlib-metadata` backport for the `importlib.metadata` module, use `importlib-metadata >=7.1.0,<8; python_version < '3.10'`. To install `colorama` on Windows (but omit it on other platforms), use `colorama >=0.4.6,<5; platform_system == "Windows"`.

Markers are combined with `and`, `or`, and parentheses, e.g., `aiohttp >=3.7.4,<4; (sys_platform != 'win32' or implementation_name != 'pypy') and python_version >= '3.10'`. Note that versions within markers must be quoted, while versions _outside_ of markers must _not_ be quoted.

---
Showing chars 28000-35223 of 35223.