from __future__ import print_function, division
from collections import OrderedDict as OD
import itertools  # For Cartesian product
from astropy.io import fits  # For reading FITS binary tables
from astropy.table import Table  # For FITS table to pandas DataFrame conversion
import numpy as np  # Core numerical library
import pandas as pd # For tables ("DataFrame"s)


"""
This module contains code to load the model grid database table, constuct
model flux arrays, and interpolate those arrays to higher resolution.

Adam D. Thomas 2015 - 2017
"""



class Grid_description(object):
    """
    Class to hold information about N-dimensional arrays - the names of the
    parameters corresponding to each dimension, the values of the parameters
    along each dimension, etc.
    """
    def __init__(self, param_names, param_value_arrs):
        """
        Initialise an instance with useful attributes, including mappings
        between important quantities that define the grid.
        param_names: List of parameter names as strings
        param_value_arrs: List of lists of parameter values over the grid,
                          where sublists correspond to param_names.
        Note that NebulaBayes code relies on the dictionaries below being ordered.
        """
        assert len(param_names) == len(param_value_arrs)
        # Record some basic info
        self.param_names = param_names
        self.param_values_arrs = param_value_arrs
        self.ndim = len(param_names)
        self.shape = tuple( [len(arr) for arr in param_value_arrs ] )
        self.n_gridpoints = np.product( self.shape )

        # Define mappings for easily extracting data about the grid
        self.paramName2ind = OD(zip(param_names, range(self.ndim)))
        #self.ind2paramName = OD(zip(range(self.ndim), param_names))
        self.paramName2paramValueArr = OD(zip(param_names, param_value_arrs))
        # self.ind2paramValueArr = OD(zip(range(self.ndim), param_value_arrs))

        self.paramNameAndValue2arrayInd = OD()
        for p, arr in self.paramName2paramValueArr.items():
            for i,v in enumerate(arr):
                self.paramNameAndValue2arrayInd[(p,v)] = i
        # So self.paramNameAndValue2ArrayInd[(p,v)] will give the index along
        # the "p" axis where the parameter with name "p" has value v.

        self.paramName2paramMinMax = OD( (p,(a.min(), a.max())) for p,a in 
                                          self.paramName2paramValueArr.items() )



class NB_Grid(Grid_description):
    """
    Simple class to hold n_dimensional grid arrays, along with a description of
    the grid.
    Will hold an grid of the same shape for each emission line.
    """
    def __init__(self, param_names, param_value_arrs):
        """ Initialise """
        super(NB_Grid, self).__init__(param_names, param_value_arrs)
        self.grids = OD()  # We rely on this being ordered
        # For the raw grids, this "grids" dict holds arrays under the line name
        # directly.  For the interpolated grids, the "grids" attribute holds
        # other dicts, named e.g. "No_norm" and "Hbeta_norm", corresponding to
        # different normalisations.  When we normalise we may lose information
        # (where the normalising grid has value zero), so we need multiple
        # copies of the interpolated grids for different normalisations.  Every
        # time we want a new normalisation, we add another dict (set of grids)
        # to the "grids" dict.



def initialise_grids(grid_file, grid_params, lines_list, interpd_grid_shape):
    """
    Initialise grids and return Raw_grids and Interpd_grids.  Called when
    initialising an NB_Model instance.
    The Raw_grids and Interpd_grids objects are instances of the NB_Grid class
    defined above.
    grid_file: The filename of a csv, FITS or compressed FITS (fits.gz)
               table of photoionisation model grid fluxes. Each gridpoint
               (point in parameter space) is a row in this table.  The
               values of the grid parameters for each row are defined in a
               column for each parameter.  There is a column of fluxes for
               each modelled emission line. 
               No assumptions are made about the order of the gridpoints
               (rows) in the table.  Spacing of grid values along an axis
               may be uneven, but the full grid is required to be a regular,
               n-dimensional rectangular grid.  Unnecessary columns will be
               ignored but extra rows are not permitted.  Model fluxes will
               be normalised by NebulaBayes.
               Any non-finite fluxes (e.g. nans) will be set to zero.
    grid_params: List of the unique names of the grid parameters as strings.
                 This list sets the order of the grid dimensions, i.e. the
                 order in which arrays in NebulaBayes will be indexed.  The
                 names must each match a column header in grid_file.
    interpd_grid_shape: A tuple of integers giving the size of each
                    dimension of the interpolated flux grids.  The order of
                    the integers corresponds to the order of parameters in
                    grid_params.  The default is 15 gridpoints along each
                    dimension.  These values have a major impact on the
                    speed of the grid interpolation.
    """
    # Load database table containing the model grid output
    DF_grid = read_gridfile(grid_file, lines_list)
    
    # Construct raw flux grids
    Raw_grids = construct_raw_grids(DF_grid, grid_params, lines_list)

    # Interpolate flux grids
    Interpd_grids = interpolate_flux_arrays(Raw_grids, interpd_grid_shape)

    return Raw_grids, Interpd_grids



def read_gridfile(grid_file, lines_list):
    """
    Read the model grid table file, and return a pandas DataFrame.
    See initialise_grids for more info.
    """
    
    print("Loading input grid table...") 
    if grid_file.endswith(".csv"):
        DF_grid = pd.read_table(grid_file, header=0, delimiter=",")
    elif grid_file.endswith((".fits", ".fits.gz")):
        BinTableHDU_0 = fits.getdata(grid_file, 0)
        DF_grid = Table(BinTableHDU_0).to_pandas()
    else:
        raise ValueError("grid_file has unknown file extension")

    # print("Cleaning input grid table...")
    # Remove any whitespace from column names
    DF_grid.rename(inplace=True, columns={c:c.strip() for c in DF_grid.columns})
    for line in lines_list: # Ensure line columns are a numeric data type
        DF_grid[line] = pd.to_numeric(DF_grid[line], errors="raise")
        DF_grid[line] = DF_grid[line].astype("float64") # Ensure double precision

    # Clean and check the model data:
    for line in lines_list:
        # Check that all emission lines in input are also in the model data:
        if not line in DF_grid.columns:
            raise ValueError("Measured emission line " + line +
                             " was not found in the model data.")
        # Set any non-finite model fluxes to zero.  Is this the wrong thing to
        # do?  It's documented at least, in NB0_Main.py.
        DF_grid.loc[~np.isfinite(DF_grid[line].values), line] = 0
        # Check that all model flux values are non-negative:
        if np.sum(DF_grid[line].values < 0) != 0:
            raise ValueError("A model flux value for emission line " +
                             line + " is negative.")

    return DF_grid



def construct_raw_grids(DF_grid, grid_params, lines_list):
    """
    Construct arrays of flux grids from the input flux table.
    DF_grid: pandas DataFrame table holding the predicted fluxes of the model grid.
    Params:  Object holding parameter names, corresponding to columns in DF_grid.
    lines_list: list of names of emission lines of interest, corresponding to
                columns in DF_grid.
    """
    # Set up raw grid...

    # Determine the list of parameter values for the raw grid:
    # List of arrays; each array holds the grid values for a parameter:
    param_val_arrs_raw = []
    for p in grid_params:
        # Ensure we have a sorted list of unique values for each parameter:
        param_val_arrs_raw.append( np.sort( np.unique( DF_grid[p].values ) ) )
    # Initialise a grid object to hold the raw grids:
    Raw_grids = NB_Grid(grid_params, param_val_arrs_raw)

    # Check that the input database table is the right length:
    # (This is equivalent to checking that we have a rectangular grid, e.g.
    # without missing values.  The spacing does not need to be uniform.)
    if Raw_grids.n_gridpoints != len(DF_grid):
        raise ValueError("The input model grid table does not " + 
                         "have a consistent length.")

    #--------------------------------------------------------------------------
    # Construct the raw model grids as a multidimensional array for each line
    print("Building flux arrays for the model grids...")
    # We use an inefficient method for building the model grids because we're
    # not assuming anything about the order of the rows in the input table.
    # First reduce DF_grid to include only the required columns:
    columns = grid_params + lines_list
    DF_grid = DF_grid[ columns ]
    for emission_line in lines_list: # Initialise new (emission_line,flux_array)
        # item in dictionary, as an array of nans:
        Raw_grids.grids[emission_line] = np.zeros( Raw_grids.shape ) + np.nan
    # Iterate over rows in the input model grid table:
    for row_tuple in DF_grid.itertuples(index=False, name=None):
        # row_tuple is not a namedtuple, since I set name=None.  I don't want
        # namedtuples => columns names would need to be valid python identifiers
        # and there would be a limit of 255 columns
        row_vals = dict(zip(columns,row_tuple)) # Maps col names to row values
        # Generate the value of each grid parameter for this row (in order)
        row_p_vals = ( (p, row_vals[p]) for p in grid_params )
        # List the grid indices associated with the param values for this row
        row_p_inds = [Raw_grids.paramNameAndValue2arrayInd[(p,v)] for p,v in row_p_vals]
        for line in lines_list: # Iterate emission lines
            # Write the line flux value for this gridpoint into the correct
            # location in the flux array for this line:
            Raw_grids.grids[line][tuple(row_p_inds)] = row_vals[line]

    arr_n_bytes = Raw_grids.grids[lines_list[0]].nbytes
    n_lines = len(lines_list)
    print( """Number of bytes in raw grid flux arrays: {0} for 1 emission line, 
    {1} total for all {2} lines""".format( arr_n_bytes, arr_n_bytes*n_lines,
                                                                    n_lines ) )

    return Raw_grids



def interpolate_flux_arrays(Raw_grids, interpd_shape):
    """
    Interpolate emission line grids, using linear interpolation, and in 
    arbitrary dimensions.
    We do not normalise the grid fluxes to the norm_line fluxes here (they're
    normalised just before calculating the likelihood), and we store the
    interpolated grids under the name "No_norm".
    Note that we require that the spacing in the interpolated grids is uniform,
    becuase we'll be assuming this when integrating to marginalise PDFs, and
    also we'll be using matplotlib.pyplot.imshow to show an image of PDFs on the
    interpolated array, and imshow (as you would expect) assumes "evenly-spaced"
    pixels.
    """
    print("Interpolating model emission line flux grids to shape {0}...".format(
                                                          tuple(interpd_shape)))

    # Initialise NB_Grid object for interpolated arrays
    # First we find the interpolated values of the parameters
    val_arrs_interp = []
    for i, (p, n) in enumerate(zip(Raw_grids.param_names, interpd_shape)):
        p_min, p_max = Raw_grids.paramName2paramMinMax[p]
        val_arrs_interp.append( np.linspace(p_min, p_max, n) )
    
    Interpd_grids = NB_Grid(list(Raw_grids.param_names), val_arrs_interp)
    Interpd_grids.grids["No_norm"] = OD()
    
    # Check that the interpolated grid has uniform spacing in each dimension:
    for arr in Interpd_grids.param_values_arrs:
        arr_diff = np.diff(arr)
        assert np.allclose(arr_diff, arr_diff[0])

    # Create class for carrying out the interpolation:
    Interpolator = RegularGridResampler(Raw_grids.param_values_arrs, Interpd_grids.shape)
    # Iterate emission lines, doing the interpolation:
    for emission_line, raw_flux_arr in Raw_grids.grids.items():
        print("Interpolating for {0}...".format(emission_line))
        interp_vals, interp_arr = Interpolator(raw_flux_arr)
        assert np.all(np.isfinite(interp_arr))
        Interpd_grids.grids["No_norm"][emission_line] = interp_arr
        for a1, a2 in zip(interp_vals, Interpd_grids.param_values_arrs):
            assert np.array_equal(a1, a2)


    n_lines = len(Interpd_grids.grids["No_norm"])
    line_0, arr_0 = list(Interpd_grids.grids["No_norm"].items())[0]
    print("Number of bytes in interpolated grid flux arrays:")
    print("  {0} for 1 emission line, {1} total for all {2} lines".format(
                                   arr_0.nbytes, arr_0.nbytes*n_lines, n_lines))

    # Set negative values to zero: (there shouldn't be any, since we're using
    # linear interpolation)
    for a in Interpd_grids.grids["No_norm"].values():
        np.clip(a, 0., None, out=a)

    return Interpd_grids



class RegularGridResampler(object):
    """
    Interpolate a regular grid in arbitrary dimensions to uniform sampling
    in each dimension ("re-grid the data"), potentially to a higher resolution.
    Linear interpolation is used.

    The RegularGridResampler is initialised with an input grid shape and an
    output grid shape, to be ready to interpolate from the input shape to the 
    output shape.  Each call then provides different grid data to be
    interpolated; this code is optimised for doing the same interpolation
    on many different grids of the same shape.

    The input grid data must be defined on a regular grid, but the grid spacing
    may be uneven.  The output grid will have even spacing, and the "corner"
    gridpoints and values will be the same as in the input grid.

    -- Parameters --
    in_points : tuple of ndarray of float, with shapes (m1, ), ..., (mn, )
        The points defining the regular grid in n dimensions.

    out_shape : tuple of ints
        The number of evenly spaced interpolated points in each dimension for
        output interpolated grids

    -- Notes --
    Based on the same method as is used in the scipy RegularGridInterpolator,
    which itself is based on code by Johannes Buchner, see
    https://github.com/JohannesBuchner/regulargrid

    The method is as follows (consider just one interpolation point for now):
    Iterate over "edges", which are the 2**ndim points around the interpolation
    point that are relevant to the interpolation.  An "edge" is on the "lower"
    or "upper" side of the interpolated point in a given dimension.  Each of the
    2**ndim "edges" contributes to the interpolated value.
    For each "edge", find the total weight.  The weight for each dimension comes
    from the distance in that dimension between the interpolation point and the
    "edge", and the total weight for the "edge" is the product of the weights
    for each dimension.
    The final interpolated value is the sum of contributions from each of the
    2**ndim "edges", where the contribution from each edge is the product of the
    edge value and its associated total weight.

    In practice the code is vectorised, so we do this for all interpolated
    points at once, and we use a slightly different order of calculations to
    minimise the work that needs to be done when repeating the interpolation on
    new data.
    """
    def __init__(self, in_points, out_shape):
        self.in_points = [np.asarray(p) for p in in_points]
        self.in_shape = tuple(len(p) for p in in_points)
        self.ndim = len(in_points)
        self.out_shape = tuple(out_shape)
        
        for p in self.in_points:
            if p.ndim != 1:
                raise ValueError("Points arrays must be 1D")
            if np.any(np.diff(p) <= 0.):
                raise ValueError("Points arrays must be strictly ascending")
        if len(out_shape) != self.ndim:
            raise ValueError("The output array must have the same number of "
                             "dimensions as the input array")
        for n_p in out_shape:
            if n_p < 2:
                raise ValueError("Each output dimension needs at least 2 points")
        self.out_points = [np.linspace(p[0], p[-1], n_p) for p,n_p in zip(
                                                     self.in_points, out_shape)]
        
        # Find indices of the lower edge for each interpolated point in each
        # dimension:
        self.lower_edge_inds = []
        # We calculate the distance from the interpolated point to the lower
        # edge in units where the distance from the lower to the upper edge is 1.
        self.norm_distances = []
        # Iterate dimensions:
        for p_out, p_in in zip(self.out_points, self.in_points):
            # p_out and p_in are a series of coordinate values for this dimension
            i_vec = np.searchsorted(p_in, p_out) - 1
            np.clip(i_vec, 0, p_in.size - 2, out=i_vec)
            self.lower_edge_inds.append(i_vec)
            p_in_diff = np.diff(p_in) # p_in_diff[j] is p_in[j+1] - p_in[j]
            # Use fancy indexing:
            self.norm_distances.append((p_out - p_in[i_vec]) / p_in_diff[i_vec])

        # Find weights:
        self._find_weights()

        # Find fancy indices for each edge:
        prod_arr = self._cartesian_prod(self.lower_edge_inds)
        fancy_inds_lower = tuple(prod_arr[:,i] for i in range(self.ndim))
        # The fancy indices are for the edge which corresponds to the "lower"
        # edge position in each dimension, and will extract the edge values
        # from the input grid array for every interpolated point at once
        self.fancy_inds_all = {}
        for all_j in itertools.product(*[[0,1] for _ in range(self.ndim)]):
            self.fancy_inds_all[all_j] = tuple(a + j for j,a in zip(all_j,
                                                              fancy_inds_lower))
            # We do this calculation here and store the results because it is
            # surprsingly slow and otherwise we'd need to do it for every
            # emission line (this approach does take a lot of memory though)


    def _find_weights(self):
        """
        Find the weights that are necessary for linear interpolation
        """
        # Weights for upper edge for interpolation positions in each dimension:
        weights_upper = self.norm_distances
        # The norm_distances are from the lower edge.  The weighting is such
        # that if this distance is large, we favour the upper edge.
        upper_all = self._cartesian_prod(weights_upper)
        lower_all = 1 - upper_all
        # These two arrays have shape (n_iterp_points, ndim)
        weights_all_l_u = [lower_all, upper_all]

        # Calculate the weight for each edge.  We do this for every interpolated
        # point at once.
        # The 2**ndim edges are identified by keys (j0, j1, ..., jn) where
        # j == 0 is for the lower edge in a dimension; j == 1 is for the upper edge.
        weights = {} # We'll have a vector of weights for each edge; the vector
        # has one entry for each interpolated point
        for all_j in itertools.product(*[[0,1] for _ in range(self.ndim)]):
            combined_weights = np.ones(upper_all.shape[0]) # Length n_iterp_points
            for k,j in enumerate(all_j):
                combined_weights *= weights_all_l_u[j][:,k]
                # For j = 0 use "lower_all", and for j = 1, use "upper_all".
                # We multiply the weights for each dimension to obtain the total
                # weight for this edge for each interpolated point.
            weights[all_j] = combined_weights
            # The weights for this edge are in a 1D array, which has a length
            # equal to the total number of points in the grid.

        self.weights = weights


    def _cartesian_prod(self, arrays, out=None):
        """
        Generate a cartesian product of input arrays recursively.
        Copied from:
        https://stackoverflow.com/questions/1208118/
                using-numpy-to-build-an-array-of-all-combinations-of-two-arrays
        https://stackoverflow.com/questions/28684492/
                                     numpy-equivalent-of-itertools-product?rq=1
        This method is much faster than constructing a numpy array using
        itertools.product().

        -- Parameters --
        arrays : list of array-like
            1-D arrays to form the cartesian product of.
        out : ndarray
            Array to place the cartesian product in.

        -- Returns --
        out : ndarray
            2-D array of shape (M, len(arrays)) containing cartesian products
            formed of input arrays.

        -- Example --
        >>> self._cartesian_prod(([1, 2, 3], [4, 5], [6, 7]))
        array([[1, 4, 6],
               [1, 4, 7],
               [1, 5, 6],
               [1, 5, 7],
               [2, 4, 6],
               [2, 4, 7],
               [2, 5, 6],
               [2, 5, 7],
               [3, 4, 6],
               [3, 4, 7],
               [3, 5, 6],
               [3, 5, 7]])

        """
        arrays = [np.asarray(x) for x in arrays]
        dtype = arrays[0].dtype

        n = np.prod([x.size for x in arrays])
        if out is None:
            out = np.zeros((n, len(arrays)), dtype=dtype)

        m = n // arrays[0].size
        out[:,0] = np.repeat(arrays[0], m)
        if arrays[1:]:
            self._cartesian_prod(arrays[1:], out=out[0:m, 1:])
            for j in range(1, arrays[0].size):
                out[j*m:(j+1)*m, 1:] = out[0:m, 1:]

        return out


    def __call__(self, in_grid_values):
        """
        Evaluate linear interpolation to resample a regular grid.

        -- Parameters --
        in_grid_values : ndarray
            Array holding the grid values of the grid to be resampled.
        """
        if in_grid_values.shape != self.in_shape:
            raise ValueError("Shape of grid array doesn't match shape of this "
                             "RegularGridResampler")

        out_values = np.zeros(np.product(self.out_shape)) # 1D for now
        # Iterate edges, adding the contribution from each edge to the
        # interpolated values
        for all_j, edge_weights in self.weights.items():
            edge_fancy_inds = self.fancy_inds_all[all_j]
            out_values += in_grid_values[edge_fancy_inds] * edge_weights

        # Reshape the array
        out_grid_values = out_values.reshape(self.out_shape)

        return self.out_points, out_grid_values


