"""Drawtext-safe escaping helpers.

FFmpeg's drawtext filter treats ``:`` as an option separator, ``'`` as a
quoting character and ``\`` as an escape character inside the filtergraph
description — and the whole filtergraph is itself parsed by a shell-like
quoting layer on top.  Rather than fighting three stacked quoting layers we
write risky text (titles) to a temp file and reference it with ``textfile=``,
and escape the short rank badges ("#3") with :func:`escape_text`.
"""
import os
import tempfile


def escape_text(text):
    """Escape *text* for use in a drawtext ``text=`` option.

    Escapes, in order: backslash, colon, apostrophe, percent (drawtext can
    expand ``%{...}`` expansions).  The result must be embedded in the
    filtergraph string inside single quotes.
    """
    text = str(text)
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\u2019")  # typographic apostrophe — safe literal
    text = text.replace("%", "\\%")
    return text


def write_text_file(text, directory=None, prefix="vrs_text_"):
    """Write *text* to a UTF-8 temp file and return its absolute path.

    The caller is responsible for the file's lifetime (the render pipeline
    keeps a staging dir and removes it wholesale).
    """
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".txt", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(text))
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path
