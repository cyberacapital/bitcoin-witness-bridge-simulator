"""A reference implementation of the Praxis witness-encryption scheme at toy parameters.

Transcribed from the definitions in the authors' Lean development
(https://github.com/psivesely/praxis-lean, Apache-2.0). Research code: toy
parameters give no security, and nothing here has been reviewed.
"""
from .field import Q
from .groups import BN254Groups, ExponentGroups
from .kummer import forms_of, weight_products, z_of
from .params import ParamError, Params
from .qap import QAP
from .relation import (Gate, and_equals, and_gate, bit_equals, digest, dot, in_shape,
                       is_witness, number_bits, pad, product_equals, xor_equals, xor_gate)
from .scheme import (Ciphertext, Coins, c_x, decrypt, encrypt, exponents, find_small_multiple,
                     honest_parts, r_int, sample_coins, valid_coins)
