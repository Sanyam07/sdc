# *****************************************************************************
# Copyright (c) 2020, Intel Corporation All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#     Redistributions of source code must retain the above copyright notice,
#     this list of conditions and the following disclaimer.
#
#     Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions and the following disclaimer in the documentation
#     and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
# OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
# OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,
# EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
# *****************************************************************************

import numpy
import pandas

from functools import partial

from numba import prange
from numba.extending import register_jitable
from numba.types import (float64, Boolean, Integer, NoneType, Number,
                         Omitted, StringLiteral, UnicodeType)

from sdc.datatypes.common_functions import _sdc_pandas_series_align
from sdc.datatypes.hpat_pandas_series_rolling_types import SeriesRollingType
from sdc.hiframes.pd_series_type import SeriesType
from sdc.utilities.prange_utils import parallel_chunks
from sdc.utilities.sdc_typing_utils import TypeChecker
from sdc.utilities.utils import sdc_overload_method, sdc_register_jitable


# disabling parallel execution for rolling due to numba issue https://github.com/numba/numba/issues/5098
sdc_rolling_overload = partial(sdc_overload_method, parallel=False)


hpat_pandas_series_rolling_docstring_tmpl = """
    Intel Scalable Dataframe Compiler User Guide
    ********************************************
    Pandas API: pandas.core.window.Rolling.{method_name}
{limitations_block}
    Examples
    --------
    .. literalinclude:: ../../../examples/series/rolling/series_rolling_{method_name}.py
       :language: python
       :lines: 27-
       :caption: {example_caption}
       :name: ex_series_rolling_{method_name}

    .. command-output:: python ./series/rolling/series_rolling_{method_name}.py
       :cwd: ../../../examples

    .. seealso::
        :ref:`Series.rolling <pandas.Series.rolling>`
            Calling object with a Series.
        :ref:`DataFrame.rolling <pandas.DataFrame.rolling>`
            Calling object with a DataFrame.
        :ref:`Series.{method_name} <pandas.Series.{method_name}>`
            Similar method for Series.
        :ref:`DataFrame.{method_name} <pandas.DataFrame.{method_name}>`
            Similar method for DataFrame.

    Intel Scalable Dataframe Compiler Developer Guide
    *************************************************

    Pandas Series method :meth:`pandas.Series.rolling.{method_name}()` implementation.

    .. only:: developer

    Test: python -m sdc.runtests -k sdc.tests.test_rolling.TestRolling.test_series_rolling_{method_name}

    Parameters
    ----------
    self: :class:`pandas.Series.rolling`
        input arg{extra_params}

    Returns
    -------
    :obj:`pandas.Series`
         returns :obj:`pandas.Series` object
"""


@sdc_register_jitable
def arr_apply(arr, func):
    """Apply function for values"""
    return func(arr)


@sdc_register_jitable
def arr_corr(x, y):
    """Calculate correlation of values"""
    if len(x) == 0:
        return numpy.nan

    return numpy.corrcoef(x, y)[0, 1]


@sdc_register_jitable
def arr_nonnan_count(arr):
    """Count non-NaN values"""
    return len(arr) - numpy.isnan(arr).sum()


@sdc_register_jitable
def _moment(arr, moment):
    mn = numpy.mean(arr)
    s = numpy.power((arr - mn), moment)

    return numpy.mean(s)


@sdc_register_jitable
def arr_kurt(arr):
    """Calculate unbiased kurtosis of values"""
    n = len(arr)
    if n < 4:
        return numpy.nan

    m2 = _moment(arr, 2)
    m4 = _moment(arr, 4)
    val = 0 if m2 == 0 else m4 / m2 ** 2.0

    if (n > 2) & (m2 > 0):
        val = 1.0/(n-2)/(n-3) * ((n**2-1.0)*m4/m2**2.0 - 3*(n-1)**2.0)

    return val


@sdc_register_jitable
def arr_max(arr):
    """Calculate maximum of values"""
    if len(arr) == 0:
        return numpy.nan

    return arr.max()


@sdc_register_jitable
def arr_mean(arr):
    """Calculate mean of values"""
    if len(arr) == 0:
        return numpy.nan

    return arr.mean()


@sdc_register_jitable
def arr_median(arr):
    """Calculate median of values"""
    if len(arr) == 0:
        return numpy.nan

    return numpy.median(arr)


@sdc_register_jitable
def arr_min(arr):
    """Calculate minimum of values"""
    if len(arr) == 0:
        return numpy.nan

    return arr.min()


@sdc_register_jitable
def arr_quantile(arr, q):
    """Calculate quantile of values"""
    if len(arr) == 0:
        return numpy.nan

    return numpy.quantile(arr, q)


@sdc_register_jitable
def _moment(arr, moment):
    mn = numpy.mean(arr)
    s = numpy.power((arr - mn), moment)

    return numpy.mean(s)


@sdc_register_jitable
def arr_skew(arr):
    """Calculate unbiased skewness of values"""
    n = len(arr)
    if n < 3:
        return numpy.nan

    m2 = _moment(arr, 2)
    m3 = _moment(arr, 3)
    val = 0 if m2 == 0 else m3 / m2 ** 1.5

    if (n > 2) & (m2 > 0):
        val = numpy.sqrt((n - 1.0) * n) / (n - 2.0) * m3 / m2 ** 1.5

    return val


@sdc_register_jitable
def arr_std(arr, ddof):
    """Calculate standard deviation of values"""
    return arr_var(arr, ddof) ** 0.5


@sdc_register_jitable
def arr_var(arr, ddof):
    """Calculate unbiased variance of values"""
    length = len(arr)
    if length in [0, ddof]:
        return numpy.nan

    return numpy.var(arr) * length / (length - ddof)


def gen_hpat_pandas_series_rolling_impl(rolling_func):
    """Generate series rolling methods implementations based on input func"""
    def impl(self):
        win = self._window
        minp = self._min_periods

        input_series = self._data
        input_arr = input_series._data
        length = len(input_arr)
        output_arr = numpy.empty(length, dtype=float64)

        def apply_minp(arr, minp):
            finite_arr = arr[numpy.isfinite(arr)]
            if len(finite_arr) < minp:
                return numpy.nan
            else:
                return rolling_func(finite_arr)

        boundary = min(win, length)
        for i in prange(boundary):
            arr_range = input_arr[:i + 1]
            output_arr[i] = apply_minp(arr_range, minp)

        for i in prange(boundary, length):
            arr_range = input_arr[i + 1 - win:i + 1]
            output_arr[i] = apply_minp(arr_range, minp)

        return pandas.Series(output_arr, input_series._index, name=input_series._name)

    return impl


def gen_hpat_pandas_series_rolling_ddof_impl(rolling_func):
    """Generate series rolling methods implementations with parameter ddof"""
    def impl(self, ddof=1):
        win = self._window
        minp = self._min_periods

        input_series = self._data
        input_arr = input_series._data
        length = len(input_arr)
        output_arr = numpy.empty(length, dtype=float64)

        def apply_minp(arr, ddof, minp):
            finite_arr = arr[numpy.isfinite(arr)]
            if len(finite_arr) < minp:
                return numpy.nan
            else:
                return rolling_func(finite_arr, ddof)

        boundary = min(win, length)
        for i in prange(boundary):
            arr_range = input_arr[:i + 1]
            output_arr[i] = apply_minp(arr_range, ddof, minp)

        for i in prange(boundary, length):
            arr_range = input_arr[i + 1 - win:i + 1]
            output_arr[i] = apply_minp(arr_range, ddof, minp)

        return pandas.Series(output_arr, input_series._index, name=input_series._name)

    return impl


hpat_pandas_rolling_series_kurt_impl = register_jitable(
    gen_hpat_pandas_series_rolling_impl(arr_kurt))
hpat_pandas_rolling_series_max_impl = register_jitable(
    gen_hpat_pandas_series_rolling_impl(arr_max))
hpat_pandas_rolling_series_median_impl = register_jitable(
    gen_hpat_pandas_series_rolling_impl(arr_median))
hpat_pandas_rolling_series_min_impl = register_jitable(
    gen_hpat_pandas_series_rolling_impl(arr_min))
hpat_pandas_rolling_series_skew_impl = register_jitable(
    gen_hpat_pandas_series_rolling_impl(arr_skew))
hpat_pandas_rolling_series_std_impl = register_jitable(
    gen_hpat_pandas_series_rolling_ddof_impl(arr_std))
hpat_pandas_rolling_series_var_impl = register_jitable(
    gen_hpat_pandas_series_rolling_ddof_impl(arr_var))


@sdc_register_jitable
def pop_sum(value, nfinite, result):
    """Calculate the window sum without old value."""
    if numpy.isfinite(value):
        nfinite -= 1
        result -= value

    return nfinite, result


@sdc_register_jitable
def put_sum(value, nfinite, result):
    """Calculate the window sum with new value."""
    if numpy.isfinite(value):
        nfinite += 1
        result += value

    return nfinite, result


@sdc_register_jitable
def result_or_nan(nfinite, minp, result):
    """Get result taking into account min periods."""
    if nfinite < minp:
        return numpy.nan

    return result


@sdc_register_jitable
def mean_result_or_nan(nfinite, minp, result):
    """Get result mean taking into account min periods."""
    if nfinite == 0 or nfinite < minp:
        return numpy.nan

    return result / nfinite


def gen_sdc_pandas_series_rolling_impl(pop, put, get_result=result_or_nan,
                                       init_result=numpy.nan):
    """Generate series rolling methods implementations based on pop/put funcs"""
    def impl(self):
        win = self._window
        minp = self._min_periods

        input_series = self._data
        input_arr = input_series._data
        length = len(input_arr)
        output_arr = numpy.empty(length, dtype=float64)

        chunks = parallel_chunks(length)
        for i in prange(len(chunks)):
            chunk = chunks[i]
            nfinite = 0
            result = init_result

            prelude_start = max(0, chunk.start - win + 1)
            prelude_stop = min(chunk.start, prelude_start + win)

            interlude_start = prelude_stop
            interlude_stop = min(prelude_start + win, chunk.stop)

            for idx in range(prelude_start, prelude_stop):
                value = input_arr[idx]
                nfinite, result = put(value, nfinite, result)

            for idx in range(interlude_start, interlude_stop):
                value = input_arr[idx]
                nfinite, result = put(value, nfinite, result)
                output_arr[idx] = get_result(nfinite, minp, result)

            for idx in range(interlude_stop, chunk.stop):
                put_value = input_arr[idx]
                pop_value = input_arr[idx - win]
                nfinite, result = put(put_value, nfinite, result)
                nfinite, result = pop(pop_value, nfinite, result)
                output_arr[idx] = get_result(nfinite, minp, result)

        return pandas.Series(output_arr, input_series._index,
                             name=input_series._name)
    return impl


sdc_pandas_series_rolling_mean_impl = gen_sdc_pandas_series_rolling_impl(
    pop_sum, put_sum, get_result=mean_result_or_nan, init_result=0.)
sdc_pandas_series_rolling_sum_impl = gen_sdc_pandas_series_rolling_impl(
    pop_sum, put_sum, init_result=0.)


@sdc_rolling_overload(SeriesRollingType, 'apply')
def hpat_pandas_series_rolling_apply(self, func, raw=None):

    ty_checker = TypeChecker('Method rolling.apply().')
    ty_checker.check(self, SeriesRollingType)

    raw_accepted = (Omitted, NoneType, Boolean)
    if not isinstance(raw, raw_accepted) and raw is not None:
        ty_checker.raise_exc(raw, 'bool', 'raw')

    def hpat_pandas_rolling_series_apply_impl(self, func, raw=None):
        win = self._window
        minp = self._min_periods

        input_series = self._data
        input_arr = input_series._data
        length = len(input_arr)
        output_arr = numpy.empty(length, dtype=float64)

        def culc_apply(arr, func, minp):
            finite_arr = arr.copy()
            finite_arr[numpy.isinf(arr)] = numpy.nan
            if len(finite_arr) < minp:
                return numpy.nan
            else:
                return arr_apply(finite_arr, func)

        boundary = min(win, length)
        for i in prange(boundary):
            arr_range = input_arr[:i + 1]
            output_arr[i] = culc_apply(arr_range, func, minp)

        for i in prange(boundary, length):
            arr_range = input_arr[i + 1 - win:i + 1]
            output_arr[i] = culc_apply(arr_range, func, minp)

        return pandas.Series(output_arr, input_series._index, name=input_series._name)

    return hpat_pandas_rolling_series_apply_impl


@sdc_rolling_overload(SeriesRollingType, 'corr')
def hpat_pandas_series_rolling_corr(self, other=None, pairwise=None):

    ty_checker = TypeChecker('Method rolling.corr().')
    ty_checker.check(self, SeriesRollingType)

    # TODO: check `other` is Series after a circular import of SeriesType fixed
    # accepted_other = (bool, Omitted, NoneType, SeriesType)
    # if not isinstance(other, accepted_other) and other is not None:
    #     ty_checker.raise_exc(other, 'Series', 'other')

    accepted_pairwise = (bool, Boolean, Omitted, NoneType)
    if not isinstance(pairwise, accepted_pairwise) and pairwise is not None:
        ty_checker.raise_exc(pairwise, 'bool', 'pairwise')

    nan_other = isinstance(other, (Omitted, NoneType)) or other is None

    def hpat_pandas_rolling_series_corr_impl(self, other=None, pairwise=None):
        win = self._window
        minp = self._min_periods

        main_series = self._data
        main_arr = main_series._data
        main_arr_length = len(main_arr)

        if nan_other == True:  # noqa
            other_arr = main_arr
        else:
            other_arr = other._data

        other_arr_length = len(other_arr)
        length = max(main_arr_length, other_arr_length)
        output_arr = numpy.empty(length, dtype=float64)

        def calc_corr(main, other, minp):
            # align arrays `main` and `other` by size and finiteness
            min_length = min(len(main), len(other))
            main_valid_indices = numpy.isfinite(main[:min_length])
            other_valid_indices = numpy.isfinite(other[:min_length])
            valid = main_valid_indices & other_valid_indices

            if len(main[valid]) < minp:
                return numpy.nan
            else:
                return arr_corr(main[valid], other[valid])

        for i in prange(min(win, length)):
            main_arr_range = main_arr[:i + 1]
            other_arr_range = other_arr[:i + 1]
            output_arr[i] = calc_corr(main_arr_range, other_arr_range, minp)

        for i in prange(win, length):
            main_arr_range = main_arr[i + 1 - win:i + 1]
            other_arr_range = other_arr[i + 1 - win:i + 1]
            output_arr[i] = calc_corr(main_arr_range, other_arr_range, minp)

        return pandas.Series(output_arr)

    return hpat_pandas_rolling_series_corr_impl


@sdc_rolling_overload(SeriesRollingType, 'count')
def hpat_pandas_series_rolling_count(self):

    ty_checker = TypeChecker('Method rolling.count().')
    ty_checker.check(self, SeriesRollingType)

    def hpat_pandas_rolling_series_count_impl(self):
        win = self._window

        input_series = self._data
        input_arr = input_series._data
        length = len(input_arr)
        output_arr = numpy.empty(length, dtype=float64)

        boundary = min(win, length)
        for i in prange(boundary):
            arr_range = input_arr[:i + 1]
            output_arr[i] = arr_nonnan_count(arr_range)

        for i in prange(boundary, length):
            arr_range = input_arr[i + 1 - win:i + 1]
            output_arr[i] = arr_nonnan_count(arr_range)

        return pandas.Series(output_arr, input_series._index, name=input_series._name)

    return hpat_pandas_rolling_series_count_impl


def _hpat_pandas_series_rolling_cov_check_types(self, other=None,
                                                pairwise=None, ddof=1):
    """Check types of parameters of series.rolling.cov()"""
    ty_checker = TypeChecker('Method rolling.cov().')
    ty_checker.check(self, SeriesRollingType)

    accepted_other = (bool, Omitted, NoneType, SeriesType)
    if not isinstance(other, accepted_other) and other is not None:
        ty_checker.raise_exc(other, 'Series', 'other')

    accepted_pairwise = (bool, Boolean, Omitted, NoneType)
    if not isinstance(pairwise, accepted_pairwise) and pairwise is not None:
        ty_checker.raise_exc(pairwise, 'bool', 'pairwise')

    if not isinstance(ddof, (int, Integer, Omitted)):
        ty_checker.raise_exc(ddof, 'int', 'ddof')


def _gen_hpat_pandas_rolling_series_cov_impl(other, align_finiteness=False):
    """Generate series.rolling.cov() implementation based on series alignment"""
    nan_other = isinstance(other, (Omitted, NoneType)) or other is None

    def _impl(self, other=None, pairwise=None, ddof=1):
        win = self._window
        minp = self._min_periods

        main_series = self._data
        if nan_other == True:  # noqa
            other_series = main_series
        else:
            other_series = other

        main_aligned, other_aligned = _sdc_pandas_series_align(main_series, other_series,
                                                               finiteness=align_finiteness)
        count = (main_aligned + other_aligned).rolling(win).count()
        bias_adj = count / (count - ddof)

        def mean(series):
            # cannot call return series.rolling(win, min_periods=minp).mean()
            # due to different float rounding in new and old implementations
            # TODO: fix this during optimizing of covariance
            input_arr = series._data
            length = len(input_arr)
            output_arr = numpy.empty(length, dtype=float64)

            def apply_minp(arr, minp):
                finite_arr = arr[numpy.isfinite(arr)]
                if len(finite_arr) < minp:
                    return numpy.nan
                else:
                    return arr_mean(finite_arr)

            boundary = min(win, length)
            for i in prange(boundary):
                arr_range = input_arr[:i + 1]
                output_arr[i] = apply_minp(arr_range, minp)

            for i in prange(boundary, length):
                arr_range = input_arr[i + 1 - win:i + 1]
                output_arr[i] = apply_minp(arr_range, minp)

            return pandas.Series(output_arr, series._index, name=series._name)

        return (mean(main_aligned * other_aligned) - mean(main_aligned) * mean(other_aligned)) * bias_adj

    return _impl


@sdc_rolling_overload(SeriesRollingType, 'cov')
def hpat_pandas_series_rolling_cov(self, other=None, pairwise=None, ddof=1):
    _hpat_pandas_series_rolling_cov_check_types(self, other=other,
                                                pairwise=pairwise, ddof=ddof)

    return _gen_hpat_pandas_rolling_series_cov_impl(other, align_finiteness=True)


@sdc_rolling_overload(SeriesRollingType, '_df_cov')
def hpat_pandas_series_rolling_cov(self, other=None, pairwise=None, ddof=1):
    _hpat_pandas_series_rolling_cov_check_types(self, other=other,
                                                pairwise=pairwise, ddof=ddof)

    return _gen_hpat_pandas_rolling_series_cov_impl(other)


@sdc_rolling_overload(SeriesRollingType, 'kurt')
def hpat_pandas_series_rolling_kurt(self):

    ty_checker = TypeChecker('Method rolling.kurt().')
    ty_checker.check(self, SeriesRollingType)

    return hpat_pandas_rolling_series_kurt_impl


@sdc_rolling_overload(SeriesRollingType, 'max')
def hpat_pandas_series_rolling_max(self):

    ty_checker = TypeChecker('Method rolling.max().')
    ty_checker.check(self, SeriesRollingType)

    return hpat_pandas_rolling_series_max_impl


@sdc_overload_method(SeriesRollingType, 'mean')
def hpat_pandas_series_rolling_mean(self):

    ty_checker = TypeChecker('Method rolling.mean().')
    ty_checker.check(self, SeriesRollingType)

    return sdc_pandas_series_rolling_mean_impl


@sdc_rolling_overload(SeriesRollingType, 'median')
def hpat_pandas_series_rolling_median(self):

    ty_checker = TypeChecker('Method rolling.median().')
    ty_checker.check(self, SeriesRollingType)

    return hpat_pandas_rolling_series_median_impl


@sdc_rolling_overload(SeriesRollingType, 'min')
def hpat_pandas_series_rolling_min(self):

    ty_checker = TypeChecker('Method rolling.min().')
    ty_checker.check(self, SeriesRollingType)

    return hpat_pandas_rolling_series_min_impl


@sdc_rolling_overload(SeriesRollingType, 'quantile')
def hpat_pandas_series_rolling_quantile(self, quantile, interpolation='linear'):

    ty_checker = TypeChecker('Method rolling.quantile().')
    ty_checker.check(self, SeriesRollingType)

    if not isinstance(quantile, Number):
        ty_checker.raise_exc(quantile, 'float', 'quantile')

    str_types = (Omitted, StringLiteral, UnicodeType)
    if not isinstance(interpolation, str_types) and interpolation != 'linear':
        ty_checker.raise_exc(interpolation, 'str', 'interpolation')

    def hpat_pandas_rolling_series_quantile_impl(self, quantile, interpolation='linear'):
        if quantile < 0 or quantile > 1:
            raise ValueError('quantile value not in [0, 1]')
        if interpolation != 'linear':
            raise ValueError('interpolation value not "linear"')

        win = self._window
        minp = self._min_periods

        input_series = self._data
        input_arr = input_series._data
        length = len(input_arr)
        output_arr = numpy.empty(length, dtype=float64)

        def calc_quantile(arr, quantile, minp):
            finite_arr = arr[numpy.isfinite(arr)]
            if len(finite_arr) < minp:
                return numpy.nan
            else:
                return arr_quantile(finite_arr, quantile)

        boundary = min(win, length)
        for i in prange(boundary):
            arr_range = input_arr[:i + 1]
            output_arr[i] = calc_quantile(arr_range, quantile, minp)

        for i in prange(boundary, length):
            arr_range = input_arr[i + 1 - win:i + 1]
            output_arr[i] = calc_quantile(arr_range, quantile, minp)

        return pandas.Series(output_arr, input_series._index, name=input_series._name)

    return hpat_pandas_rolling_series_quantile_impl


@sdc_rolling_overload(SeriesRollingType, 'skew')
def hpat_pandas_series_rolling_skew(self):

    ty_checker = TypeChecker('Method rolling.skew().')
    ty_checker.check(self, SeriesRollingType)

    return hpat_pandas_rolling_series_skew_impl


@sdc_rolling_overload(SeriesRollingType, 'std')
def hpat_pandas_series_rolling_std(self, ddof=1):

    ty_checker = TypeChecker('Method rolling.std().')
    ty_checker.check(self, SeriesRollingType)

    if not isinstance(ddof, (int, Integer, Omitted)):
        ty_checker.raise_exc(ddof, 'int', 'ddof')

    return hpat_pandas_rolling_series_std_impl


@sdc_overload_method(SeriesRollingType, 'sum')
def hpat_pandas_series_rolling_sum(self):

    ty_checker = TypeChecker('Method rolling.sum().')
    ty_checker.check(self, SeriesRollingType)

    return sdc_pandas_series_rolling_sum_impl


@sdc_rolling_overload(SeriesRollingType, 'var')
def hpat_pandas_series_rolling_var(self, ddof=1):

    ty_checker = TypeChecker('Method rolling.var().')
    ty_checker.check(self, SeriesRollingType)

    if not isinstance(ddof, (int, Integer, Omitted)):
        ty_checker.raise_exc(ddof, 'int', 'ddof')

    return hpat_pandas_rolling_series_var_impl


hpat_pandas_series_rolling_apply.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'apply',
    'example_caption': 'Calculate the rolling apply.',
    'limitations_block':
    """
    Limitations
    -----------
    Supported ``raw`` only can be `None` or `True`. Parameters ``args``, ``kwargs`` unsupported.
    Series elements cannot be max/min float/integer. Otherwise SDC and Pandas results are different.
    """,
    'extra_params':
    """
    func:
        A single value producer
    raw: :obj:`bool`
        False : passes each row or column as a Series to the function.
        True or None : the passed function will receive ndarray objects instead.
    """
})

hpat_pandas_series_rolling_corr.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'corr',
    'example_caption': 'Calculate rolling correlation.',
    'limitations_block':
    """
    Limitations
    -----------
    Series elements cannot be max/min float/integer. Otherwise SDC and Pandas results are different.
    Resulting Series has default index and name.
    """,
    'extra_params':
    """
    other: :obj:`Series`
        Other Series.
    pairwise: :obj:`bool`
        Not relevant for Series.
    """
})

hpat_pandas_series_rolling_count.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'count',
    'example_caption': 'Count of any non-NaN observations inside the window.',
    'limitations_block': '',
    'extra_params': ''
})

hpat_pandas_series_rolling_cov.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'cov',
    'example_caption': 'Calculate rolling covariance.',
    'limitations_block':
    """
    Limitations
    -----------
    Series elements cannot be max/min float/integer. Otherwise SDC and Pandas results are different.
    Resulting Series has default index and name.
    """,
    'extra_params':
    """
    other: :obj:`Series`
        Other Series.
    pairwise: :obj:`bool`
        Not relevant for Series.
    ddof: :obj:`int`
        Delta Degrees of Freedom.
    """
})

hpat_pandas_series_rolling_kurt.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'kurt',
    'example_caption': 'Calculate unbiased rolling kurtosis.',
    'limitations_block': '',
    'extra_params': ''
})

hpat_pandas_series_rolling_max.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'max',
    'example_caption': 'Calculate the rolling maximum.',
    'limitations_block': '',
    'extra_params': ''
})

hpat_pandas_series_rolling_mean.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'mean',
    'example_caption': 'Calculate the rolling mean of the values.',
    'limitations_block':
    """
    Limitations
    -----------
    Series elements cannot be max/min float/integer. Otherwise SDC and Pandas results are different.
    """,
    'extra_params': ''
})

hpat_pandas_series_rolling_median.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'median',
    'example_caption': 'Calculate the rolling median.',
    'limitations_block': '',
    'extra_params': ''
})

hpat_pandas_series_rolling_min.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'min',
    'example_caption': 'Calculate the rolling minimum.',
    'limitations_block': '',
    'extra_params': ''
})

hpat_pandas_series_rolling_quantile.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'quantile',
    'example_caption': 'Calculate the rolling quantile.',
    'limitations_block':
    """
    Limitations
    -----------
    Supported ``interpolation`` only can be `'linear'`.
    Series elements cannot be max/min float/integer. Otherwise SDC and Pandas results are different.
    """,
    'extra_params':
    """
    quantile: :obj:`float`
        Quantile to compute. 0 <= quantile <= 1.
    interpolation: :obj:`str`
        This optional parameter specifies the interpolation method to use.
    """
})

hpat_pandas_series_rolling_skew.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'skew',
    'example_caption': 'Unbiased rolling skewness.',
    'limitations_block': '',
    'extra_params': ''
})

hpat_pandas_series_rolling_std.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'std',
    'example_caption': 'Calculate rolling standard deviation.',
    'limitations_block':
    """
    Limitations
    -----------
    Series elements cannot be max/min float/integer. Otherwise SDC and Pandas results are different.
    """,
    'extra_params':
    """
    ddof: :obj:`int`
        Delta Degrees of Freedom.
    """
})

hpat_pandas_series_rolling_sum.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'sum',
    'example_caption': 'Calculate rolling sum of given Series.',
    'limitations_block':
    """
    Limitations
    -----------
    Series elements cannot be max/min float/integer. Otherwise SDC and Pandas results are different.
    """,
    'extra_params': ''
})

hpat_pandas_series_rolling_var.__doc__ = hpat_pandas_series_rolling_docstring_tmpl.format(**{
    'method_name': 'var',
    'example_caption': 'Calculate unbiased rolling variance.',
    'limitations_block':
    """
    Limitations
    -----------
    Series elements cannot be max/min float/integer. Otherwise SDC and Pandas results are different.
    """,
    'extra_params':
    """
    ddof: :obj:`int`
        Delta Degrees of Freedom.
    """
})
