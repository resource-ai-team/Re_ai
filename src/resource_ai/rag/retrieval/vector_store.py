from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector


class Database:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    @contextmanager
    def connect(self, *, register_vector_types: bool = True) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.database_url) as connection:
            if register_vector_types:
                register_vector(connection)
            yield connection
