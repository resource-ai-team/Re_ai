from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector


class Database:
    def __init__(self, database_url: str, *, connect_timeout_seconds: int = 5) -> None:
        self.database_url = database_url
        self.connect_timeout_seconds = connect_timeout_seconds

    @contextmanager
    def connect(self, *, register_vector_types: bool = True) -> Iterator[psycopg.Connection]:
        with psycopg.connect(
            self.database_url, connect_timeout=self.connect_timeout_seconds
        ) as connection:
            if register_vector_types:
                register_vector(connection)
            yield connection
