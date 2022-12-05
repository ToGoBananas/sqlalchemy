# mysql/enumerated.py
# Copyright (C) 2005-2022 the SQLAlchemy authors and contributors
# <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: https://www.opensource.org/licenses/mit-license.php
# mypy: ignore-errors


import re

from .types import _StringType
from ... import exc
from ... import sql
from ... import util
from ...sql import sqltypes


class ENUM(sqltypes.NativeForEmulated, sqltypes.Enum, _StringType):
    """MySQL ENUM type."""

    __visit_name__ = "ENUM"

    native_enum = True

    def __init__(self, *enums, **kw):
        """Construct an ENUM.

        E.g.::

          Column('myenum', ENUM("foo", "bar", "baz"))

        :param enums: The range of valid values for this ENUM.  Values in
          enums are not quoted, they will be escaped and surrounded by single
          quotes when generating the schema.  This object may also be a
          PEP-435-compliant enumerated type.

          .. versionadded: 1.1 added support for PEP-435-compliant enumerated
             types.

        :param strict: This flag has no effect.

         .. versionchanged:: The MySQL ENUM type as well as the base Enum
            type now validates all Python data values.

        :param charset: Optional, a column-level character set for this string
          value.  Takes precedence to 'ascii' or 'unicode' short-hand.

        :param collation: Optional, a column-level collation for this string
          value.  Takes precedence to 'binary' short-hand.

        :param ascii: Defaults to False: short-hand for the ``latin1``
          character set, generates ASCII in schema.

        :param unicode: Defaults to False: short-hand for the ``ucs2``
          character set, generates UNICODE in schema.

        :param binary: Defaults to False: short-hand, pick the binary
          collation type that matches the column's character set.  Generates
          BINARY in schema.  This does not affect the type of data stored,
          only the collation of character data.

        """
        kw.pop("strict", None)
        self._enum_init(enums, kw)
        _StringType.__init__(self, length=self.length, **kw)

    @classmethod
    def adapt_emulated_to_native(cls, impl, **kw):
        """Produce a MySQL native :class:`.mysql.ENUM` from plain
        :class:`.Enum`.

        """
        kw.setdefault("validate_strings", impl.validate_strings)
        kw.setdefault("values_callable", impl.values_callable)
        kw.setdefault("omit_aliases", impl._omit_aliases)
        return cls(**kw)

    def _object_value_for_elem(self, elem):
        # mysql sends back a blank string for any value that
        # was persisted that was not in the enums; that is, it does no
        # validation on the incoming data, it "truncates" it to be
        # the blank string.  Return it straight.
        if elem == "":
            return elem
        else:
            return super()._object_value_for_elem(elem)

    def __repr__(self):
        return util.generic_repr(
            self, to_inspect=[ENUM, _StringType, sqltypes.Enum]
        )


class SET(_StringType):
    """MySQL SET type."""

    __visit_name__ = "SET"

    def __init__(self, *values, **kw):
        """Construct a SET.

        E.g.::

          Column('myset', SET("foo", "bar", "baz"))


        The list of potential values is required in the case that this
        set will be used to generate DDL for a table, or if the
        :paramref:`.SET.retrieve_as_bitwise` flag is set to True.

        :param values: The range of valid values for this SET. The values
          are not quoted, they will be escaped and surrounded by single
          quotes when generating the schema.

        :param convert_unicode: Same flag as that of
         :paramref:`.String.convert_unicode`.

        :param collation: same as that of :paramref:`.String.collation`

        :param charset: same as that of :paramref:`.VARCHAR.charset`.

        :param ascii: same as that of :paramref:`.VARCHAR.ascii`.

        :param unicode: same as that of :paramref:`.VARCHAR.unicode`.

        :param binary: same as that of :paramref:`.VARCHAR.binary`.

        :param retrieve_as_bitwise: if True, the data for the set type will be
          persisted and selected using an integer value, where a set is coerced
          into a bitwise mask for persistence.  MySQL allows this mode which
          has the advantage of being able to store values unambiguously,
          such as the blank string ``''``.   The datatype will appear
          as the expression ``col + 0`` in a SELECT statement, so that the
          value is coerced into an integer value in result sets.
          This flag is required if one wishes
          to persist a set that can store the blank string ``''`` as a value.

          .. warning::

            When using :paramref:`.mysql.SET.retrieve_as_bitwise`, it is
            essential that the list of set values is expressed in the
            **exact same order** as exists on the MySQL database.

          .. versionadded:: 1.0.0

        """
        self.retrieve_as_bitwise = kw.pop("retrieve_as_bitwise", False)
        self.values = tuple(values)
        if not self.retrieve_as_bitwise and "" in values:
            raise exc.ArgumentError(
                "Can't use the blank value '' in a SET without "
                "setting retrieve_as_bitwise=True"
            )
        if self.retrieve_as_bitwise:
            self._bitmap = {
                value: 2**idx for idx, value in enumerate(self.values)
            }
            self._bitmap.update(
                (2**idx, value) for idx, value in enumerate(self.values)
            )
        length = max([len(v) for v in values] + [0])
        kw.setdefault("length", length)
        super().__init__(**kw)

    def column_expression(self, colexpr):
        if self.retrieve_as_bitwise:
            return sql.type_coerce(
                sql.type_coerce(colexpr, sqltypes.Integer) + 0, self
            )
        else:
            return colexpr

    def result_processor(self, dialect, coltype):
        if self.retrieve_as_bitwise:

            def process(value):
                if value is not None:
                    value = int(value)

                    return set(util.map_bits(self._bitmap.__getitem__, value))
                else:
                    return None

        else:
            super_convert = super().result_processor(dialect, coltype)

            def process(value):
                if isinstance(value, str):
                    # MySQLdb returns a string, let's parse
                    if super_convert:
                        value = super_convert(value)
                    return set(re.findall(r"[^,]+", value))
                else:
                    # mysql-connector-python does a naive
                    # split(",") which throws in an empty string
                    if value is not None:
                        value.discard("")
                    return value

        return process

    def bind_processor(self, dialect):
        super_convert = super().bind_processor(dialect)
        if self.retrieve_as_bitwise:

            def process(value):
                if value is None:
                    return None
                elif isinstance(value, (int, str)):
                    if super_convert:
                        return super_convert(value)
                    else:
                        return value
                else:
                    int_value = 0
                    for v in value:
                        int_value |= self._bitmap[v]
                    return int_value

        else:

            def process(value):
                # accept strings and int (actually bitflag) values directly
                if value is not None and not isinstance(value, (int, str)):
                    value = ",".join(value)

                if super_convert:
                    return super_convert(value)
                else:
                    return value

        return process

    def adapt(self, impltype, **kw):
        kw["retrieve_as_bitwise"] = self.retrieve_as_bitwise
        return util.constructor_copy(self, impltype, *self.values, **kw)

    def __repr__(self):
        return util.generic_repr(
            self,
            to_inspect=[SET, _StringType],
            additional_kw=[
                ("retrieve_as_bitwise", False),
            ],
        )
