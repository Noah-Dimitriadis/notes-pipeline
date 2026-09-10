from __future__ import annotations

import typer

app = typer.Typer(help="Lecture notes pipeline: audio + slides + notes -> markdown.")


@app.callback()
def main() -> None:
    """Lecture notes pipeline. Commands land in M8."""


if __name__ == "__main__":
    app()
