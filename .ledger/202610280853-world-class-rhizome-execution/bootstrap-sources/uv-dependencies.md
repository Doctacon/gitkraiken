# Managing dependencies | uv

## [Dependency fields](#dependency-fields)

Dependencies of the project are defined in several fields:

*   [`project.dependencies`](#project-dependencies): Published dependencies.
*   [`project.optional-dependencies`](#optional-dependencies): Published optional dependencies, or "extras".
*   [`dependency-groups`](#dependency-groups): Local dependencies for development.
*   [`tool.uv.sources`](#dependency-sources): Alternative sources for dependencies during development.

Note

The `project.dependencies` and `project.optional-dependencies` fields can be used even if the project isn't going to be published. `dependency-groups` are a recently standardized feature and may not be supported by all tools yet.

uv supports modifying the project's dependencies with `uv add` and `uv remove`, but dependency metadata can also be updated by editing the `pyproject.toml` directly.

## [Adding dependencies](#adding-dependencies)

To add a dependency:

`[](#__codelineno-0-1)$ uv add httpx`

An entry will be added in the `project.dependencies` field:

pyproject.toml

`[](#__codelineno-1-1)[project] [](#__codelineno-1-2)name = "example" [](#__codelineno-1-3)version = "0.1.0" [](#__codelineno-1-4)dependencies = ["httpx>=0.27.2"]`

The [`--dev`](#development-dependencies), [`--group`](#dependency-groups), or [`--optional`](#optional-dependencies) flags can be used to add dependencies to an alternative field.

The dependency will include a constraint, e.g., `>=0.27.2`, for the most recent, compatible version of the package. The kind of bound can be adjusted with [`--bounds`](../../../reference/settings/#add-bounds), or the constraint can be provided directly:

`[](#__codelineno-2-1)$ uv add "httpx>=0.20"`

When adding a dependency from a source other than a package registry, uv will add an entry in the sources field. For example, when adding `httpx` from GitHub:

`[](#__codelineno-3-1)$ uv add "httpx @ git+https://github.com/encode/httpx"`

The `pyproject.toml` will include a [Git source entry](#git):

pyproject.toml

`[](#__codelineno-4-1)[project] [](#__codelineno-4-2)name = "example" [](#__codelineno-4-3)version = "0.1.0" [](#__codelineno-4-4)dependencies = [ [](#__codelineno-4-5)    "httpx", [](#__codelineno-4-6)] [](#__codelineno-4-7) [](#__codelineno-4-8)[tool.uv.sources] [](#__codelineno-4-9)httpx = { git = "https://github.com/encode/httpx" }`

If a dependency cannot be used, uv will display an error:

`[](#__codelineno-5-1)$ uv add "httpx>9999" [](#__codelineno-5-2)  × No solution found when resolving dependencies: [](#__codelineno-5-3)  ╰─▶ Because only httpx<=1.0.0b0 is available and your project depends on httpx>9999, [](#__codelineno-5-4)      we can conclude that your project's requirements are unsatisfiable.`

### [Importing dependencies from requirements files](#importing-dependencies-from-requirements-files)

Dependencies declared in a `requirements.txt` file can be added to the project with the `-r` option:

`[](#__codelineno-6-1)uv add -r requirements.txt`

See the [pip migration guide](../../../guides/migration/pip-to-project/#importing-requirements-files) for more details.

## [Removing dependencies](#removing-dependencies)

To remove a dependency:

`[](#__codelineno-7-1)$ uv remove httpx`

The `--dev`, `--group`, or `--optional` flags can be used to remove a dependency from a specific table.

If a [source](#dependency-sources) is defined for the removed dependency, and there are no other references to the dependency, it will also be removed.

## [Changing dependencies](#changing-dependencies)

To change an existing dependency, e.g., to use a different constraint for `httpx`:

`[](#__codelineno-8-1)$ uv add "httpx>0.1.0"`

Note

In this example, we are changing the constraints for the dependency in the `pyproject.toml`. The locked version of the dependency will only change if necessary to satisfy the new constraints. To force the package version to update to the latest within the constraints, use `--upgrade-package <name>`, e.g.:

`[](#__codelineno-9-1)$ uv add "httpx>0.1.0" --upgrade-package httpx`

See the [lockfile](../sync/#upgrading-locked-package-versions) documentation for more details on upgrading packages.

Requesting a different dependency source will update the `tool.uv.sources` table, e.g., to use `httpx` from a local path during development:

`[](#__codelineno-10-1)$ uv add "httpx @ ../httpx"`

## [Platform-specific dependencies](#platform-specific-dependencies)

To ensure that a dependency is only installed on a specific platform or on specific Python versions, use [environment markers](https://peps.python.org/pep-0508/#environment-markers).

For example, to install `jax` on Linux, but not on Windows or macOS:

`[](#__codelineno-11-1)$ uv add "jax; sys_platform == 'linux'"`

The resulting `pyproject.toml` will then include the environment marker in the dependency definition:

pyproject.toml

`[](#__codelineno-12-1)[project] [](#__codelineno-12-2)name = "project" [](#__codelineno-12-3)version = "0.1.0" [](#__codelineno-12-4)requires-python = ">=3.11" [](#__codelineno-12-5)dependencies = ["jax; sys_platform == 'linux'"]`

Similarly, to include `numpy` on Python 3.11 and later:

`[](#__codelineno-13-1)$ uv add "numpy; python_version >= '3.11'"`

See Python's [environment marker](https://peps.python.org/pep-0508/#environment-markers) documentation for a complete enumeration of the available markers and operators.

Tip

Dependency sources can also be [changed per-platform](#platform-specific-sources).

## [Project dependencies](#project-dependencies)

The `project.dependencies` table represents the dependencies that are used when uploading to PyPI or building a wheel. Individual dependencies are specified using [dependency specifiers](https://packaging.python.org/en/latest/specifications/dependency-specifiers/) syntax, and the table follows the [PEP 621](https://packaging.python.org/en/latest/specifications/pyproject-toml/) standard.

`project.dependencies` defines the list of packages that are required for the project, along with any version constraints to use when installing them. Each entry includes a dependency name and may include version constraints, extras, or environment markers for platform-specific packages. For example:

pyproject.toml

`[](#__codelineno-14-1)[project] [](#__codelineno-14-2)name = "albatross" [](#__codelineno-14-3)version = "0.1.0" [](#__codelineno-14-4)dependencies = [ [](#__codelineno-14-5)  # Any version in this range [](#__codelineno-14-6)  "tqdm >=4.66.2,<5", [](#__codelineno-14-7)  # Exactly this version of torch [](#__codelineno-14-8)  "torch ==2.2.2", [](#__codelineno-14-9)  # Install transformers with the torch extra [](#__codelineno-14-10)  "transformers[torch] >=4.39.3,<5", [](#__codelineno-14-11)  # Only install this package on older python versions [](#__codelineno-14-12)  # See "Environment Markers" for more information [](#__codelineno-14-13)  "importlib_metadata >=7.1.0,<8; python_version < '3.10'", [](#__codelineno-14-14)  "mollymawk ==0.1.0" [](#__codelineno-14-15)]`

## [Dependency sources](#dependency-sources)

The `tool.uv.sources` table extends the standard dependency tables with alternative dependency sources, which are used during development.

Dependency sources add support for common patterns that are not supported by the `project.dependencies` standard, like editable installations and relative paths. For example, to install `foo` from a directory relative to the project root:

pyproject.toml

`[](#__codelineno-15-1)[project] [](#__codelineno-15-2)name = "example" [](#__codelineno-15-3)version = "0.1.0" [](#__codelineno-15-4)dependencies = ["foo"] [](#__codelineno-15-5) [](#__codelineno-15-6)[tool.uv.sources] [](#__codelineno-15-7)foo = { path = "./packages/foo" }`

The following dependency sources are supported by uv:

*   [Index](#index): A package resolved from a specific package index.
*   [Git](#git): A Git repository.
*   [URL](#url): A remote wheel or source distribution.
*   [Path](#path): A local wheel, source distribution, or project directory.
*   [Workspace](#workspace-member): A member of the current workspace.

Important

Sources are only respected by uv. If another tool is used, only the definitions in the standard project tables will be used. If another tool is being used for development, any metadata provided in the source table will need to be re-specified in the other tool's format.

### [Index](#index)

To add a Python package from a specific index, use the `--index` option:

`[](#__codelineno-16-1)$ uv add torch --index pytorch=https://download.pytorch.org/whl/cpu`

uv will store the index in `[[tool.uv.index]]` and add a `[tool.uv.sources]` entry:

pyproject.toml

`[](#__codelineno-17-1)[project] [](#__codelineno-17-2)dependencies = ["torch"] [](#__codelineno-17-3) [](#__codelineno-17-4)[tool.uv.sources] [](#__codelineno-17-5)torch = { index = "pytorch" } [](#__codelineno-17-6) [](#__codelineno-17-7)[[tool.uv.index]] [](#__codelineno-17-8)name = "pytorch" [](#__codelineno-17-9)url = "https://download.pytorch.org/whl/cpu"`

If the index is already configured, you can select it by name (this feature is in preview):

`[](#__codelineno-18-1)$ uv add --preview-features index-by-name torch --index pytorch`

Tip

The above example will only work on x86-64 Linux, due to the specifics of the PyTorch index. See the [PyTorch guide](../../../guides/integration/pytorch/) for more information about setting up PyTorch.

Using an `index` source _pins_ a package to the given index — it will not be downloaded from other indexes.

When defining an index, an `explicit` flag can be included to indicate that the index should _only_ be used for packages that explicitly specify it in `tool.uv.sources`. If `explicit` is not set, other packages may be resolved from the index, if not found elsewhere.

pyproject.toml

`[](#__codelineno-19-1)[[tool.uv.index]] [](#__codelineno-19-2)name = "pytorch" [](#__codelineno-19-3)url = "https://download.pytorch.org/whl/cpu" [](#__codelineno-19-4)explicit = true`

### [Git](#git)

To add a Git dependency source, prefix a Git-compatible URL with `git+`.

For example:

`[](#__codelineno-20-1)$ # Install over HTTP(S). [](#__codelineno-20-2)$ uv add git+https://github.com/encode/httpx [](#__codelineno-20-3) [](#__codelineno-20-4)$ # Install over SSH. [](#__codelineno-20-5)$ uv add git+ssh://[[email protected]](/cdn-cgi/l/email-protection)/encode/httpx`

pyproject.toml

`[](#__codelineno-21-1)[project] [](#__codelineno-21-2)dependencies = ["httpx"] [](#__codelineno-21-3) [](#__codelineno-21-4)[tool.uv.sources] [](#__codelineno-21-5)httpx = { git = "https://github.com/encode/httpx" }`

Specific Git references can be requested, e.g., a tag:

`[](#__codelineno-22-1)$ uv add git+https://github.com/encode/httpx --tag 0.27.0`

pyproject.toml

`[](#__codelineno-23-1)[project] [](#__codelineno-23-2)dependencies = ["httpx"] [](#__codelineno-23-3) [](#__codelineno-23-4)[tool.uv.sources] [](#__codelineno-23-5)httpx = { git = "https://github.com/encode/httpx", tag = "0.27.0" }`

Or, a branch:

`[](#__codelineno-24-1)$ uv add git+https://github.com/encode/httpx --branch main`

pyproject.toml

`[](#__codelineno-25-1)[project] [](#__codelineno-25-2)dependencies = ["httpx"] [](#__codelineno-25-3) [](#__codelineno-25-4)[tool.uv.sources] [](#__codelineno-25-5)httpx = { git = "https://github.com/encode/httpx", branch = "main" }`

Or, a revision (commit):

`[](#__codelineno-26-1)$ uv add git+https://github.com/encode/httpx --rev 326b9431c761e1ef1e00b9f760d1f654c8db48c6`

pyproject.toml

`[](#__codelineno-27-1)[project] [](#__codelineno-27-2)dependencies = ["httpx"] [](#__codelineno-27-3) [](#__codelineno-27-4)[tool.uv.sources] [](#__codelineno-27-5)httpx = { git = "https://github.com/encode/httpx", rev = "326b9431c761e1ef1e00b9f760d1f654c8db48c6" }`

A `subdirectory` may be specified if the package isn't in the repository root:

`[](#__codelineno-28-1)$ uv add git+https://github.com/langchain-ai/langchain#subdirectory=libs/langchain`

pyproject.toml

`[](#__codelineno-29-1)[project] [](#__codelineno-29-2)dependencies = ["langchain"] [](#__codelineno-29-3) [](#__codelineno-29-4)[tool.uv.sources] [](#__codelineno-29-5)langchain = { git = "https://github.com/langchain-ai/langchain", subdirectory = "libs/langchain" }`

Support for [Git LFS](https://git-lfs.com) is also configurable per source. By default, Git LFS objects will not be fetched.

`[](#__codelineno-30-1)$ uv add --lfs git+https://github.com/astral-sh/lfs-cowsay`

pyproject.toml

`[](#__codelineno-31-1)[project] [](#__codelineno-31-2)dependencies = ["lfs-cowsay"] [](#__codelineno-31-3) [](#__codelineno-31-4)[tool.uv.sources] [](#__codelineno-31-5)lfs-cowsay = { git = "https://github.com/astral-sh/lfs-cowsay", lfs = true }`

*   When `lfs = true`, uv will always fetch LFS objects for this Git source.
*   When `lfs = false`, uv will never fetch LFS objects for this Git source.
*   When omitted, the `UV_GIT_LFS` environment variable is used for all Git sources without an explicit `lfs` configuration.

Important

Ensure Git LFS is installed and configured on your system before attempting to install sources using Git LFS, otherwise a build failure can occur.

### [URL](#url)

To add a URL source, provide a `https://` URL to either a wheel (ending in `.whl`) or a source distribution (typically ending in `.tar.gz` or `.zip`; see [here](../../resolution/#source-distribution) for all supported formats).

For example:

`[](#__codelineno-32-1)$ uv add "https://files.pythonhosted.org/packages/5c/2d/3da5bdf4408b8b2800061c339f240c1802f2e82d55e50bd39c5a881f47f0/httpx-0.27.0.tar.gz"`

Will result in a `pyproject.toml` with:

pyproject.toml

`[](#__codelineno-33-1)[project] [](#__codelineno-33-2)dependencies = ["httpx"] [](#__codelineno-33-3) [](#__codelineno-33-4)[tool.uv.sources] [](#__codelineno-33-5)httpx = { url = "https://files.pythonhosted.org/packages/5c/2d/3da5bdf4408b8b2800061c339f240c1802f2e82d55e50bd39c5a881f47f0/httpx-0.27.0.tar.gz" }`

URL dependencies can also be manually added or edited in the `pyproject.toml` with the `{ url = <url> }` syntax. A `subdirectory` may be specified if the source distribution isn't in the archive root.

### [Path](#path)

To add a path source, provide the path of a wheel (ending in `.whl`), a source distribution (typically ending in `.tar.gz` or `.zip`; see [here](../../resolution/#source-distribution) for all supported formats), or a directory containing a `pyproject.toml`.

For example:

`[](#__codelineno-34-1)$ uv add /example/foo-0.1.0-py3-none-any.whl`

Will result in a `pyproject.toml` with:

pyproject.toml

`[](#__codelineno-35-1)[project] [](#__codelineno-35-2)dependencies = ["foo"] [](#__codelineno-35-3) [](#__codelineno-35-4)[tool.uv.sources] [](#__codelineno-35-5)foo = { path = "/example/foo-0.1.0-py3-none-any.whl" }`

The path may also be a relative path:

`[](#__codelineno-36-1)$ uv add ./foo-0.1.0-py3-none-any.whl`

Or, a path to a project directory:

`[](#__codelineno-37-1)$ uv add ~/projects/bar/`

Important

When using a directory as a path dependency, uv will attempt to build and install the target as a package by default. See the [virtual dependency](#virtual-dependencies) documentation for details.

An [editable installation](#editable-dependencies) is not used for path dependencies by default. An editable installation may be requested for project directories:

`[](#__codelineno-38-1)$ uv add --editable ../projects/bar/`

Which will result in a `pyproject.toml` with:

pyproject.toml

`[](#__codelineno-39-1)[project] [](#__codelineno-39-2)dependencies = ["bar"] [](#__codelineno-39-3) [](#__codelineno-39-4)[tool.uv.sources] [](#__codelineno-39-5)bar = { path = "../projects/bar", editable = true }`

Tip

For multiple packages in the same repository, [_workspaces_](../workspaces/) may be a better fit.

### [Workspace member](#workspace-member)

To declare a dependency on a workspace member, add the member name with `{ workspace = true }`. All workspace members must be explicitly stated. Workspace members are always [editable](#editable-dependencies). See the [workspace](../workspaces/) documentation for more details on workspaces.

To source a dependency from a different workspace, `workspace` can also be a path string:

pyproject.toml

`[](#__codelineno-40-1)[tool.uv.sources] [](#__codelineno-40-2)foo = { workspace = "../other-workspace" }`

pyproject.toml

`[](#__codelineno-41-1)[project] [](#__codelineno-41-2)dependencies = ["foo==0.1.0"] [](#__codelineno-41-3) [](#__codelineno-41-4)[tool.uv.sources] [](#__codelineno-41-5)foo = { workspace = true } [](#__codelineno-41-6) [](#__codelineno-41-7)[tool.uv.workspace] [](#__codelineno-41-8)members = [ [](#__codelineno-41-9)  "packages/foo" [](#__codelineno-41-10)]`

### [Platform-specific sources](#platform-specific-sources)

You can limit a source to a given platform or Python version by providing [dependency specifiers](https://packaging.python.org/en/latest/specifications/dependency-specifiers/)\-compatible environment markers for the source.

For example, to pull `httpx` from GitHub, but only on macOS, use the following:

pyproject.toml

`[](#__codelineno-42-1)[project] [](#__codelineno-42-2)dependencies = ["httpx"] [](#__codelineno-42-3) [](#__codelineno-42-4)[tool.uv.sources] [](#__codelineno-42-5)httpx = { git = "https://github.com/encode/httpx", tag = "0.27.2", marker = "sys_platform == 'darwin'" }`

By specifying the marker on the source, uv will still include `httpx` on all platforms, but will download the source from GitHub on macOS, and fall back to PyPI on all other platforms.

### [Multiple sources](#multiple-sources)

You can specify multiple sources for a single dependency by providing a list of sources, disambiguated by [PEP 508](https://peps.python.org/pep-0508/#environment-markers)\-compatible environment markers.

For example, to pull in different `httpx` tags on macOS vs. Linux:

pyproject.toml

`[](#__codelineno-43-1)[project] [](#__codelineno-43-2)dependencies = ["httpx"] [](#__codelineno-43-3) [](#__codelineno-43-4)[tool.uv.sources] [](#__codelineno-43-5)httpx = [ [](#__codelineno-43-6)  { git = "https://github.com/encode/httpx", tag = "0.27.2", marker = "sys_platform == 'darwin'" }, [](#__codelineno-43-7)  { git = "https://github.com/encode/httpx", tag = "0.24.1", marker = "sys_platform == 'linux'" }, [](#__codelineno-43-8)]`

This strategy extends to using different indexes based on environment markers. For example, to install `torch` from different PyTorch indexes based on the platform:

pyproject.toml

`[](#__codelineno-44-1)[project] [](#__codelineno-44-2)dependencies = ["torch"] [](#__codelineno-44-3) [](#__codelineno-44-4)[tool.uv.sources] [](#__codelineno-44-5)torch = [ [](#__codelineno-44-6)  { index = "torch-cpu", marker = "platform_system == 'Darwin'"}, [](#__codelineno-44-7)  { index = "torch-gpu", marker = "platform_system == 'Linux'"}, [](#__codelineno-44-8)] [](#__codelineno-44-9) [](#__codelineno-44-10)[[tool.uv.index]] [](#__codelineno-44-11)name = "torch-cpu" [](#__codelineno-44-12)url = "https://download.pytorch.org/whl/cpu" [](#__codelineno-44-13)explicit = true [](#__codelineno-44-14) [](#__codelineno-44-15)[[tool.uv.index]] [](#__codelineno-44-16)name = "torch-gpu" [](#__codelineno-44-17)url = "https://download.pytorch.org/whl/cu130" [](#__codelineno-44-18)explicit = true`

### [Disabling sources](#disabling-sources)

To instruct uv to ignore the `tool.uv.sources` table (e.g., to simulate resolving with the package's published metadata), use the `--no-sources` flag:

`[](#__codelineno-45-1)$ uv lock --no-sources`

The use of `--no-sources` will also prevent uv from discovering any [workspace members](#workspace-member) that could satisfy a given dependency.

## [Optional dependencies](#optional-dependencies)

It is common for projects that are published as libraries to make some features optional to reduce the default dependency tree. For example, Pandas has an [`excel` extra](https://pandas.pydata.org/docs/getting_started/install.html#excel-files) and a [`plot` extra](https://pandas.pydata.org/docs/getting_started/install.html#visualization) to avoid installation of Excel parsers and `matplotlib` unless someone explicitly requires them. Extras are requested with the `package[<extra>]` syntax, e.g., `pandas[plot, excel]`.

Optional dependencies are specified in `[project.optional-dependencies]`, a TOML table that maps from extra name to its dependencies, following [dependency specifiers](#dependency-specifiers) syntax.

Optional dependencies can have entries in `tool.uv.sources` the same as normal dependencies.

pyproject.toml

`[](#__codelineno-46-1)[project] [](#__codelineno-46-2)name = "pandas" [](#__codelineno-46-3)version = "1.0.0" [](#__codelineno-46-4) [](#__codelineno-46-5)[project.optional-dependencies] [](#__codelineno-46-6)plot = [ [](#__codelineno-46-7)  "matplotlib>=3.6.3" [](#__codelineno-46-8)] [](#__codelineno-46-9)excel = [ [](#__codelineno-46-10)  "odfpy>=1.4.1", [](#__codelineno-46-11)  "openpyxl>=3.1.0", [](#__codelineno-46-12)  "python-calamine>=0.1.7", [](#__codelineno-46-13)  "pyxlsb>=1.0.10", [](#__codelineno-46-14)  "xlrd>=2.0.1", [](#__codelineno-46-15)  "xlsxwriter>=3.0.5" [](#__codelineno-46-16)]`

To add an optional dependency, use the `--optional <extra>` option:

`[](#__codelineno-47-1)$ uv add httpx --optional network`

Note

If you have optional dependencies that conflict with one another, resolution will fail unless you explicitly [declare them as conflicting](../config/#conflicting-dependencies).

Sources can also be declared as applying only to a specific optional dependency. For example, to pull `torch` from different PyTorch indexes based on an optional `cpu` or `gpu` extra:

pyproject.toml

`[](#__codelineno-48-1)[project] [](#__codelineno-48-2)dependencies = [] [](#__codelineno-48-3) [](#__codelineno-48-4)[project.optional-dependencies] [](#__codelineno-48-5)cpu = [ [](#__codelineno-48-6)  "torch", [](#__codelineno-48-7)] [](#__codelineno-48-8)gpu = [ [](#__codelineno-48-9)  "torch", [](#__codelineno-48-10)] [](#__codelineno-48-11) [](#__codelineno-48-12)[tool.uv.sources] [](#__codelineno-48-13)torch = [ [](#__codelineno-48-14)  { index = "torch-cpu", extra = "cpu" }, [](#__codelineno-48-15)  { index = "torch-gpu", extra = "gpu" }, [](#__codelineno-48-16)] [](#__codelineno-48-17) [](#__codelineno-48-18)[[tool.uv.index]] [](#__codelineno-48-19)name = "torch-cpu" [](#__codelineno-48-20)url = "https://download.pytorch.org/whl/cpu" [](#__codelineno-48-21) [](#__codelineno-48-22)[[tool.uv.index]] [](#__codelineno-48-23)name = "torch-gpu" [](#__codelineno-48-24)url = "https://download.pytorch.org/whl/cu130"`

## [Development dependencies](#development-dependencies)

Unlike optional dependencies, development dependencies are local-only and will _not_ be included in the project requirements when published to PyPI or other indexes. As such, development dependencies are not included in the `[project]` table.

Development dependencies can have entries in `tool.uv.sources` the same as normal dependencies.

To add a development dependency, use the `--dev` flag:

`[](#__codelineno-49-1)$ uv add --dev pytest`

uv uses the `[dependency-groups]` table (as defined in [PEP 735](https://peps.python.org/pep-0735/)) for declaration of development dependencies. The above command will create a `dev` group:

pyproject.toml

`[](#__codelineno-50-1)[dependency-groups] [](#__codelineno-50-2)dev = [ [](#__codelineno-50-3)  "pytest >=8.1.1,<9" [](#__codelineno-50-4)]`

The `dev` group is special-cased; there are `--dev`, `--only-dev`, and `--no-dev` flags to toggle inclusion or exclusion of its dependencies. See `--no-default-groups` to disable all default groups instead. Additionally, the `dev` group is [synced by default](#default-groups).

### [Dependency groups](#dependency-groups)

Development dependencies can be divided into multiple groups, using the `--group` flag.

For example, to add a development dependency in the `lint` group:

`[](#__codelineno-51-1)$ uv add --group lint ruff`

Which results in the following `[dependency-groups]` definition:

pyproject.toml

`[](#__codelineno-52-1)[dependency-groups] [](#__codelineno-52-2)dev = [ [](#__codelineno-52-3)  "pytest" [](#__codelineno-52-4)] [](#__codelineno-52-5)lint = [ [](#__codelineno-52-6)  "ruff" [](#__codelineno-52-7)]`

Once groups are defined, the `--all-groups`, `--no-default-groups`, `--group`, `--only-group`, and `--no-group` options can be used to include or exclude their dependencies.

Tip

The `--dev`, `--only-dev`, and `--no-dev` flags are equivalent to `--group dev`, `--only-group dev`, and `--no-group dev` respectively.

uv requires that all dependency groups are compatible with each other and resolves all groups together when creating the lockfile.

If dependencies declared in one group are not compatible with those in another group, uv will fail to resolve the requirements of the project with an error.

Note

If you have dependency groups that conflict with one another, resolution will fail unless you explicitly [declare them as conflicting](../config/#conflicting-dependencies).

### [Nesting groups](#nesting-groups)

A dependency group can include other dependency groups, e.g.:

pyproject.toml

`[](#__codelineno-53-1)[dependency-groups] [](#__codelineno-53-2)dev = [ [](#__codelineno-53-3)  {include-group = "lint"}, [](#__codelineno-53-4)  {include-group = "test"} [](#__codelineno-53-5)] [](#__codelineno-53-6)lint = [ [](#__codelineno-53-7)  "ruff" [](#__codelineno-53-8)] [](#__codelineno-53-9)test = [ [](#__codelineno-53-10)  "pytest" [](#__codelineno-53-11)]`

An included group's dependencies cannot conflict with the other dependencies declared in a group.

### [Default groups](#default-groups)

By default, uv includes the `dev` dependency group in the environment (e.g., during `uv run` or `uv sync`). The default groups to include can be changed using the `tool.uv.default-groups` setting.

pyproject.toml

`[](#__codelineno-54-1)[tool.uv] [](#__codelineno-54-2)default-groups = ["dev", "foo"]`

To enable all dependency groups by default, use `"all"` instead of listing group names:

pyproject.toml

`[](#__codelineno-55-1)[tool.uv] [](#__codelineno-55-2)default-groups = "all"`

Tip

To disable this behaviour during `uv run` or `uv sync`, use `--no-default-groups`. To exclude a specific default group, use `--no-group <name>`.

### [Group `requires-python`](#group-requires-python)

By default, dependency groups must be compatible with your project's `requires-python` range.

If a dependency group requires a different range of Python versions than your project, you can specify a `requires-python` for the group in `[tool.uv.dependency-groups]`, e.g.:

pyproject.toml

`[](#__codelineno-56-1)[project] [](#__codelineno-56-2)name = "example" [](#__codelineno-56-3)version = "0.0.0" [](#__codelineno-56-4)requires-python = ">=3.10" [](#__codelineno-56-5) [](#__codelineno-56-6)[dependency-groups] [](#__codelineno-56-7)dev = ["pytest"] [](#__codelineno-56-8) [](#__codelineno-56-9)[tool.uv.dependency-groups] [](#__codelineno-56-10)dev = {requires-python = ">=3.12"}`

### [Legacy `dev-dependencies`](#legacy-dev-dependencies)

Before `[dependency-groups]` was standardized, uv used the `tool.uv.dev-dependencies` field to specify development dependencies, e.g.:

pyproject.toml

`[](#__codelineno-57-1)[tool.uv] [](#__codelineno-57-2)dev-dependencies = [ [](#__codelineno-57-3)  "pytest" [](#__codelineno-57-4)]`

Dependencies declared in this section will be combined with the contents in the `dependency-groups.dev`. Eventually, the `dev-dependencies` field will be deprecated and removed.

Note

If a `tool.uv.dev-dependencies` field exists, `uv add --dev` will use the existing section instead of adding a new `dependency-groups.dev` section.

## [Build dependencies](#build-dependencies)

If a project is structured as a [Python package](../config/#build-systems), it may declare dependencies that are required to build the project, but not required to run it. These dependencies are specified in the `[build-system]` table under `build-system.requires`, following [PEP 518](https://peps.python.org/pep-0518/).

For example, if a project uses `setuptools` as its build backend, it should declare `setuptools` as a build dependency:

pyproject.toml

`[](#__codelineno-58-1)[project] [](#__codelineno-58-2)name = "pandas" [](#__codelineno-58-3)version = "0.1.0" [](#__codelineno-58-4) [](#__codelineno-58-5)[build-system] [](#__codelineno-58-6)requires = ["setuptools>=42"] [](#__codelineno-58-7)build-backend = "setuptools.build_meta"`

By default, uv will respect `tool.uv.sources` when resolving build dependencies. For example, to use a local version of `setuptools` for building, add the source to `tool.uv.sources`:

pyproject.toml

`[](#__codelineno-59-1)[project] [](#__codelineno-59-2)name = "pandas" [](#__codelineno-59-3)version = "0.1.0" [](#__codelineno-59-4) [](#__codelineno-59-5)[build-system] [](#__codelineno-59-6)requires = ["setuptools>=42"] [](#__codelineno-59-7)build-backend = "setuptools.build_meta" [](#__codelineno-59-8) [](#__codelineno-59-9)[tool.uv.sources] [](#__codelineno-59-10)setuptools = { path = "./packages/setuptools" }`

When publishing a package, we recommend running `uv build --no-sources` to ensure that the package builds correctly when `tool.uv.sources` is disabled, as is the case when using other build tools, like [`pypa/build`](https://github.com/pypa/build).

## [Editable dependencies](#editable-dependencies)

A regular installation of a directory with a Python package first builds a wheel and then installs that wheel into your virtual environment, copying all source files. When the package source files are edited, the virtual environment will contain outdated versions.

Editable installations solve this problem by adding a link to the project within the virtual environment (a `.

---
Showing chars 0-30000 of 35223. Use get_search_content({ responseId: "mvsdigaz3zvfau", urlIndex: 2, offset: 30000, limit: 30000 }) for the next slice.