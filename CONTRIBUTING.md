# Contributing

Thanks for wanting to share an extension.  This repository holds example
extensions from Boris FX and extensions contributed by the Silhouette
community.  Each lives in its own folder under `extensions/`, which users
install by dragging it onto Silhouette.

Just want to show something you built, or float an idea first?  Post it in
[Discussions](https://github.com/boris-fx/silhouette-extensions/discussions):
*Show and tell* or *Ideas*.  The steps below are for getting an extension
into this repository.

## 1. Open an issue first

Before you write a pull request, [open an issue](https://github.com/boris-fx/silhouette-extensions/issues/new)
describing the extension:

- **What it does**, and what it contributes (nodes, actions, panels,
  control pages, background tasks, themes, …)
- **Its id**: reverse-DNS and unique to you, e.g. `com.yourstudio.thing`.
  The `com.borisfx` prefix is reserved.
- **What it depends on**: pip packages, ML models, external services or
  accounts
- **Third-party code or models it includes or downloads**, with their
  licenses and links to the upstream projects

This lets us agree on fit, naming, and licensing before you spend time
preparing a pull request.

## 2. Prepare the extension

Add it as `extensions/<your id>/`.  The folder name must equal the `id` in
its `extension.toml`.

Every extension needs:

- an `extension.toml` with `id`, `name`, `version` (`MAJOR.MINOR.PATCH`),
  `author`, `description`, and `silhouette` (the oldest version you tested)
- a `README.md` that says what the extension does, what it needs, and how
  to use it
- a row in the extensions table of the top-level `README.md`: the website
  builds its cards, in that order, from that table

Optionally, add a `screenshot.png` (16:9 works best) to the folder; the
website shows it on the extension's card and page.

And must not contain:

- **model weights** or other large binaries.  Host them elsewhere (a
  GitHub release asset works) and download them on first use as a
  background task.  `com.borisfx.nafnet` shows how.
- **secrets** of any kind.  Register an option preference so each user
  enters their own key.
- build clutter: `__pycache__/`, `.pyc`, `.DS_Store`

If you vendor third-party source, keep its license header and
attribution, and add an entry for it to
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).  Make sure the upstream
license allows commercial use and redistribution.  Code licensed for
research or non-commercial use only can't be accepted.

Follow the habits in the scripting documentation's *Extensions* chapter
(**Help → Scripting** in Silhouette), in particular:

- import heavy packages lazily (with `tools.ensure`), never at module top
  level, and import a node's compiled dependencies in `setup()`
- set `requires_gui = true` for an all-interface extension, or guard the
  interface half with `fx.gui`
- make module-level registration idempotent, so disabling, reloading,
  and re-enabling leaves no duplicate hooks, key binds, or templates

## 3. Check it

```sh
python3 -m compileall -q extensions/<your id>
```

Install the folder into a clean Silhouette (drag it onto the window), and
check that it loads, works, disables, and re-enables cleanly.
If it contributes a node or action that should work in command-line
renders, check that too.

## 4. Open a pull request

Reference the issue in the pull request.  CI runs on every pull request.
It validates each manifest (the id format, the version format, and that
the id matches its folder) and compiles every Python file.  A maintainer will review the extension and may ask for changes.

To update an extension you contributed, open a pull request that raises
its `version`.

## License

By contributing, you agree that your contribution is licensed under this
repository's [BSD 3-Clause License](LICENSE).  Vendored third-party code
keeps its own license, as recorded in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
