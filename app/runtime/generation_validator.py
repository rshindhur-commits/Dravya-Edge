from __future__ import annotations


def _metadata(state):

    if not isinstance(state, dict):

        return None

    return state.get("metadata")


def validate_generation(*states):

    metadata_rows = [_metadata(state) for state in states]

    if any(metadata is None for metadata in metadata_rows):

        return False

    generations = {metadata.get("generation") for metadata in metadata_rows}
    schemas = {metadata.get("schema") for metadata in metadata_rows}

    return (
        len(generations) == 1
        and None not in generations
        and len(schemas) == 1
        and None not in schemas
    )


def unwrap_state(state):

    if isinstance(state, dict) and "metadata" in state and "data" in state:

        data = state.get("data") or {}

        if isinstance(data, dict):

            output = dict(data)
            output["_metadata"] = state.get("metadata")
            return output

        return data

    return state