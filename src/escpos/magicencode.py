#!/usr/bin/python
#  -*- coding: utf-8 -*-
""" Magic Encode

This module tries to convert an UTF-8 string to an encoded string for the printer.
It uses trial and error in order to guess the right codepage.
The code is based on the encoding-code in py-xml-escpos by @fvdsn.

:author: `Patrick Kanzler <dev@pkanzler.de>`_
:organization: `python-escpos <https://github.com/python-escpos>`_
:copyright: Copyright (c) 2016 Patrick Kanzler and Frédéric van der Essen
:license: GNU GPL v3
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from .constants import CODEPAGE_CHANGE
from .exceptions import CharCodeError, Error
from .capabilities import get_profile
from .codepages import CodePages
import copy
import six


class Encoder(object):
    """Takes a list of available code spaces. Picks the right one for a
    given character.

    Note: To determine the code page, it needs to do the conversion, and
    thus already knows what the final byte in the target encoding would
    be. Nevertheless, the API of this class doesn't return the byte.

    The caller use to do the character conversion itself.

        $ python -m timeit -s "{u'ö':'a'}.get(u'ö')"
        100000000 loops, best of 3: 0.0133 usec per loop

        $ python -m timeit -s "u'ö'.encode('latin1')"
        100000000 loops, best of 3: 0.0141 usec per loop
    """

    def __init__(self, codepage_map):
        self.codepages = codepage_map
        self.available_encodings = set(codepage_map.keys())
        self.used_encodings = set()

    def get_sequence(self, encoding):
        return int(self.codepages[encoding])

    def get_encoding(self, encoding):
        """Given an encoding provided by the user, will return a
        canonical encoding name; and also validate that the encoding
        is supported.

        TODO: Support encoding aliases: pc437 instead of cp437.
        """
        encoding = CodePages.get_encoding(encoding)
        if not encoding in self.codepages:
            raise ValueError((
                    'Encoding "{}" cannot be used for the current profile. '
                    'Valid encodings are: {}'
                ).format(encoding, ','.join(self.codepages.keys())))
        return encoding

    def can_encode(self, encoding, char):
        try:
            encoded = CodePages.encode(char, encoding)
            assert type(encoded) is bytes
            return encoded
        except LookupError:
            # We don't have this encoding
            return False
        except UnicodeEncodeError:
            return False

        return True

    def __encoding_sort_func(self, item):
        key, index = item
        return (
            key in self.used_encodings,
            index
        )


    def find_suitable_encoding(self, char):
        """The order of our search is a specific one:

        1. code pages that we already tried before; there is a good
           chance they might work again, reducing the search space,
           and by re-using already used encodings we might also
           reduce the number of codepage change instructiosn we have
           to send. Still, any performance gains will presumably be
           fairly minor.

        2. code pages in lower ESCPOS slots first. Presumably, they
           are more likely to be supported, so if a printer profile
           is missing or incomplete, we might increase our change
           that the code page we pick for this character is actually
           supported.
        """
        sorted_encodings = sorted(
            self.codepages.items(),
            key=self.__encoding_sort_func)

        for encoding, _ in sorted_encodings:
            if self.can_encode(encoding, char):
                # This encoding worked; at it to the set of used ones.
                self.used_encodings.add(encoding)
                return encoding


class MagicEncode(object):
    """A helper that helps us to automatically switch to the right
    code page to encode any given Unicode character.

    This will consider the printers supported codepages, according
    to the printer profile, and if a character cannot be encoded
    with the current profile, it will attempt to find a suitable one.

    If the printer does not support a suitable code page, it can
    insert an error character.

    :param encoding: If you know the current encoding of the printer
        when initializing this class, set it here. If the current
        encoding is unknown, the first character emitted will be a
        codepage switch.
    """
    def __init__(self, driver, encoding=None, disabled=False,
                 defaultsymbol='?', encoder=None):
        if disabled and not encoding:
            raise Error('If you disable magic encode, you need to define an encoding!')

        self.driver = driver
        self.encoder = encoder or Encoder(driver.profile.get_code_pages())

        self.encoding = self.encoder.get_encoding(encoding) if encoding else None
        self.defaultsymbol = defaultsymbol
        self.disabled = disabled

    def force_encoding(self, encoding):
        """Sets a fixed encoding. The change is emitted right away.

        From now one, this buffer will switch the code page anymore.
        However, it will still keep track of the current code page.
        """
        if not encoding:
            self.disabled = False
        else:
            self.write_with_encoding(encoding, None)
            self.disabled = True

    def write(self, text):
        """Write the text, automatically switching encodings.
        """

        if self.disabled:
            self.write_with_encoding(self.encoding, text)
            return

        # TODO: Currently this very simple loop means we send every
        # character individually to the printer. We can probably
        # improve performace by searching the text for the first
        # character that cannot be rendered using the current code
        # page, and then sending all of those characters at once.
        # Or, should a lower-level buffer be responsible for that?

        for char in text:
            # See if the current code page works for this character.
            # The encoder object will use a cache to be able to answer
            # this question fairly easily.
            if self.encoding and self.encoder.can_encode(self.encoding, char):
                self.write_with_encoding(self.encoding, char)
                continue

            # We have to find another way to print this character.
            # See if any of the code pages that the printer profile supports
            # can encode this character.
            encoding = self.encoder.find_suitable_encoding(char)
            if not encoding:
                self._handle_character_failed(char)
                continue

            self.write_with_encoding(encoding, char)

    def _handle_character_failed(self, char):
        """Called when no codepage was found to render a character.
        """
        # Writing the default symbol via write() allows us to avoid
        # unnecesary codepage switches.
        self.write(self.defaultsymbol)

    def write_with_encoding(self, encoding, text):
        if text is not None and type(text) is not six.text_type:
            raise Error("The supplied text has to be unicode, but is of type {type}.".format(
                type=type(text)
            ))

        # We always know the current code page; if the new codepage
        # is different, emit a change command.
        if encoding != self.encoding:
            self.encoding = encoding
            self.driver._raw(b'{}{}'.format(
                CODEPAGE_CHANGE,
                six.int2byte(self.encoder.get_sequence(encoding))
            ))

        if text:
            self.driver._raw(CodePages.encode(text, encoding, errors="replace"))