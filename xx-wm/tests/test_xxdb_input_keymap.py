"""xxdb keymap: letters map to the kernel's KEY_* codes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xxdb_input  # noqa: E402

# linux/input-event-codes.h
_KERNEL = {
    'Q': 16, 'W': 17, 'E': 18, 'R': 19, 'T': 20, 'Y': 21, 'U': 22, 'I': 23,
    'O': 24, 'P': 25, 'A': 30, 'S': 31, 'D': 32, 'F': 33, 'G': 34, 'H': 35,
    'J': 36, 'K': 37, 'L': 38, 'Z': 44, 'X': 45, 'C': 46, 'V': 47, 'B': 48,
    'N': 49, 'M': 50,
}


def test_every_letter_is_its_kernel_key():
    for letter, code in _KERNEL.items():
        assert xxdb_input._KEY_CODES[letter] == code, letter


def test_bottom_row_never_lands_on_punctuation_or_shift():
    punctuation = set(xxdb_input._PLAIN.values()) | {xxdb_input._KEY_CODES['SHIFT']}
    for letter in 'ZXCVBNM':
        assert xxdb_input._KEY_CODES[letter] not in punctuation, letter
