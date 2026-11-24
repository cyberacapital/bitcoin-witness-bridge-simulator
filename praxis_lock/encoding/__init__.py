"""Compiling statements about BN254 arithmetic into the lock's gate format."""
from . import emulated
from .circuit import Circuit, Rng
from .emulated import Big, alloc_big, assert_zero_mod, const_big
from .fields import (FP_BITS, P, Lin, embed_fq2, fp_alloc, fp_assert, fp_const, fq2_alloc,
                     fq2_assert, fq2_const, fq2_value, fq12_alloc, fq12_assert, fq12_const,
                     fq12_value)
