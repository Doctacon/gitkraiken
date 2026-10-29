# Storage | uv

## [Storage directories](#storage-directories)

uv uses the following high-level directories for storage.

For each location, uv checks for the existence of environment variables in the given order and uses the first path found.

The paths of storage directories are platform-specific. uv follows the [XDG](https://specifications.freedesktop.org/basedir-spec/latest/) conventions on Linux and macOS and the [Known Folder](https://learn.microsoft.com/en-us/windows/win32/shell/known-folders) scheme on Windows.

### [Temporary directory](#temporary-directory)

The temporary directory is used for ephemeral data.

UnixWindows

1.  `$TMPDIR`
2.  `/tmp`

1.  `%TMP%`
2.  `%TEMP%`
3.  `%USERPROFILE%`

### [Cache directory](#cache-directory)

The cache directory is used for disposable data that is useful to keep between invocations.

UnixWindows

1.  `$XDG_CACHE_HOME/uv`
2.  `$HOME/.cache/uv`

1.  `%LOCALAPPDATA%\uv\cache`
2.  `uv\cache` within [`FOLDERID_LocalAppData`](https://learn.microsoft.com/en-us/windows/win32/shell/knownfolderid#FOLDERID_LocalAppData)

### [Persistent data directory](#persistent-data-directory)

The persistent data directory is used for non-disposable data.

UnixWindows

1.  `$XDG_DATA_HOME/uv`
2.  `$HOME/.local/share/uv`
3.  `$CWD/.uv`

1.  `%APPDATA%\uv\data`
2.  `.\.uv`

### [Configuration directories](#configuration-directories)

The configuration directories are used to store changes to uv's settings.

User-level configuration

System-level configuration

### [Executable directory](#executable-directory)

The executable directory is used to store files that can be run by the user, i.e., a directory that should be on the `PATH`.

UnixWindows

1.  `$XDG_BIN_HOME`
2.  `$XDG_DATA_HOME/../bin`
3.  `$HOME/.local/bin`

1.  `%XDG_BIN_HOME%`
2.  `%XDG_DATA_HOME%\..\bin`
3.  `%USERPROFILE%\.local\bin`

## [Types of data](#types-of-data)

### [Dependency cache](#dependency-cache)

uv uses a local cache to avoid re-downloading and re-building dependencies.

By default, the cache is stored in the [cache directory](#cache-directory) but it can be overridden via command line arguments, environment variables, or settings as detailed in [the cache documentation](../../concepts/cache/#cache-directory). When the cache is disabled, the cache will be stored in a [temporary directory](#temporary-directory).

Use `uv cache dir` to show the current cache directory path.

Important

For optimal performance, the cache directory needs to be on the same filesystem as virtual environments.

### [Python versions](#python-versions)

uv can install managed [Python versions](../../concepts/python-versions/), e.g., with `uv python install`.

By default, Python versions managed by uv are stored in a `python/` subdirectory of the [persistent data directory](#persistent-data-directory), e.g., `~/.local/share/uv/python`.

Use `uv python dir` to show the Python installation directory.

Use the `UV_PYTHON_INSTALL_DIR` environment variable to override the installation directory.

Note

Changing where Python is installed will not be automatically reflected in existing virtual environments; they will keep referring to the old location, and will need to be updated manually (e.g. by re-creating them).

### [Python executables](#python-executables)

uv installs executables for [Python versions](#python-versions), e.g., `python3.13`.

By default, Python executables are stored in the [executable directory](#executable-directory).

Use `uv python dir --bin` to show the Python executable directory.

Use the `UV_PYTHON_BIN_DIR` environment variable to override the Python executable directory.

### [Tools](#tools)

uv can install Python packages as [command-line tools](../../concepts/tools/) using `uv tool install`.

By default, tools are installed in a `tools/` subdirectory of the [persistent data directory](#persistent-data-directory), e.g., `~/.local/share/uv/tools`.

Use `uv tool dir` to show the tool installation directory.

Use the `UV_TOOL_DIR` environment variable to configure the installation directory.

### [Tool executables](#tool-executables)

uv installs executables for installed [tools](#tools), e.g., `ruff`.

By default, tool executables are stored in the [executable directory](#executable-directory).

Use `uv tool dir --bin` to show the tool executable directory.

Use the `UV_TOOL_BIN_DIR` environment variable to configure the tool executable directory.

### [The uv executable](#the-uv-executable)

When using uv's [standalone installer](../installer/) to install uv, the `uv` and `uvx` executables are installed into the [executable directory](#executable-directory).

Use the `UV_INSTALL_DIR` environment variable to configure uv's installation directory.

### [Configuration files](#configuration-files)

uv's behavior can be configured through TOML files.

Configuration files are discovered in the [configuration directories](#configuration-directories).

For more details, see the [configuration files documentation](../../concepts/configuration-files/).

### [Project virtual environments](#project-virtual-environments)

When working on [projects](../../concepts/projects/), uv creates a dedicated virtual environment for each project.

By default, project virtual environments are created in `.venv` in the project or workspace root, i.e., next to the `pyproject.toml`.

Use the `UV_PROJECT_ENVIRONMENT` environment variable to override this location. For more details, see the [projects environment documentation](../../concepts/projects/config/#project-environment-path).

With the [`centralized-project-envs` preview feature](../../concepts/preview/), uv stores default project environments in the [cache directory](#cache-directory). They can be removed by `uv cache clean` or `uv cache prune` and are recreated when next needed. See the [centralized project environments](../../concepts/projects/layout/#centralized-project-environments) documentation for details.

### [Script virtual environments](#script-virtual-environments)

When running [scripts with inline metadata](../../guides/scripts/), uv creates a dedicated virtual environment for each script in the [cache directory](#cache-directory).