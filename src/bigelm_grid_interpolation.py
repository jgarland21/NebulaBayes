from __future__ import print_function, division
import numpy as np
import pandas as pd
from scipy import ndimage
from scipy.interpolate import interp1d
# import matplotlib.pyplot as plt
from .bigelm_classes import Bigelm_container, Grid_parameters, Bigelm_grid






#============================================================================
def initialise_grids(grid_file, grid_params, lines_list):
    """
    Initialise grids and return a Bigelm_container object, which will have
    attributes Params, Raw_grids.  # and Interpd_grids.
    The returned object 
    contains all necessary grid information for bigelm, and may be used as an 
    input to repeated bigelm runs to avoid recalculation of grid data in each run.
    The Raw_grids and Interpd_grids attributes are instances of the Bigelm_grid
    class defined in this module.  The Params attribute is an instance of the
    Grid_parameters class defined in this module.
    grid_file:  the filename of an ASCII csv table containing photoionisation model
                grid data in the form of a database table.
                Each gridpoint (point in parameter space) is a row in this table.
                The values of the grid parameters for each row are defined in a column
                for each parameter.
                No assumptions are made about the order of the gridpoints (rows) in the table.
                Spacing of grid values along an axis may be uneven, 
                but the full grid is required to a be a regular, n-dimensional rectangular grid.
                There is a column of fluxes for each modelled emission line, and model fluxes
                are assumed to be normalised to Hbeta
                Any non-finite fluxes (e.g. nans) will be set to zero.
    grid_params: List of the unique names of the grid parameters as strings.
                 The order is the order of the grid dimensions, i.e. the order
                 in which arrays in bigelm will be indexed.
    # interpd_grid_shape: A tuple of integers, giving the size of each dimension
    #                     of the interpolated grid.  The order of the integers
    #                     corresponds to the order of parameters in grid_params.
    #                     The default is 30 gridpoints along each dimension.  Note
    #                     that the number of interpolated gridpoints entered in
    #                     interpd_grid_shape may have a major impact on the speed
    #                     of the program.
    """

    # Load database csv table containing the model grid output
    D_table = pd.read_csv( grid_file, delimiter=",", header=0, engine="python" )
    # The python engine is slower than the C engine, but it works!
    # Remove any whitespace from column names
    D_table.rename(inplace=True, columns={c:c.strip() for c in D_table.columns})
    for line in lines_list: # Ensure line columns have float dtype
        D_table[line] = pd.to_numeric(D_table[line], errors="coerce")
        # We "coerce" errors to NaNs, which will be set to zero

    # Clean and check the model data:
    for line in lines_list:
        # Check that all emission lines in input are also in the model data:
        if not line in D_table.columns:
            raise ValueError("Measured emission line " + line +
                             " was not found in the model data.")
        # Set any non-finite model fluxes to zero:
        ####### Dodgy?!?!
        D_table[line][ ~np.isfinite( D_table[line].values ) ] = 0
        # Check that all model flux values are non-negative:
        if np.sum( D_table[line].values < 0 ) != 0:
            raise ValueError("A flux value for modelled emission line " +
                             line + " is negative.")

    # Initialise a custom class instance to hold parameter names and related
    # lists for both the raw and interpolated grids:
    Params = Grid_parameters( grid_params )

    # # Number of points for each parameter in interpolated grid, in order:
    # if interpd_grid_shape == None: # If not specified by user:
    #     interpd_grid_shape = tuple( [30]*Params.n_params ) # Default
    # else: # If specified by user, check that it's the right length:
    #     if len(interpd_grid_shape) != Params.n_params:
    #         raise ValueError("interpd_grid_shape should contain" + 
    #                          "exactly one integer for each parameter" )

    #--------------------------------------------------------------------------
    # Set up raw grid and interpolation...

    # Determine the list of parameter values for the raw grid:
    # Initialise list of arrays, with each array being the list of grid values for a parameter:
    param_val_arrs_raw = []
    for p in Params.names:
        # Ensure we have a sorted list of unique values for each parameter:
        param_val_arrs_raw.append( np.sort( np.unique( D_table[p].values ) ) )
    # Initialise a grid object to hold the raw grids, arrays of parameter values, etc.:
    Raw_grids = Bigelm_grid( param_val_arrs_raw )

    # Check that the input database table is the right length:
    # (This is equivalent to checking that we have a regular grid, e.g. without missing values)
    if Raw_grids.n_gridpoints != len(D_table):
        raise ValueError("The input model grid table does not" + 
                         "have a consistent length.")

    # # Determine the parameter values for the interpolated grid,
    # # and then initialise a grid object for the interpolated grid:
    # # Create a list of arrays, where each array contains a list of constantly-spaced values for
    # # the corresponding parameter in the interpolated grid:
    # param_val_arrs_interp = []
    # for p_arr, n in zip(Raw_grids.val_arrs, interpd_grid_shape):
    #     param_val_arrs_interp.append( np.linspace( np.min(p_arr),
    #                                                np.max(p_arr), n ) )
    # # Initialise Bigelm_grid object to hold the interpolated grids, arrays of parameter values, etc.:
    # Interpd_grids = Bigelm_grid( param_val_arrs_interp )
    # # List of interpolated grid spacing for each parameter
    # # (needed for integration and plotting later):
    # Interpd_grids.spacing = [ (val_arr[1] - val_arr[0]) for val_arr in Interpd_grids.val_arrs ]

    #--------------------------------------------------------------------------
    # Construct the raw model grids as a multidimensional array for each line
    print("Building flux arrays for the model grids...")
    # We use an inefficient method for building the model grids because we're
    # not assuming anything about the order of the rows in the input table.
    # First reduce D_table to include only the required columns:
    columns = Params.names + lines_list
    D_table = D_table[ columns ]
    for emission_line in lines_list: # Initialise new (emission_line,flux_array)
        # item in dictionary, as an array of nans:
        Raw_grids.grids[emission_line] = np.zeros( Raw_grids.shape ) + np.nan
    # Iterate over rows in the input model grid table:
    for row_tuple in D_table.itertuples(index=False, name=None):
        # row_tuple is not a namedtuple, since I set name=None.  I don't want
        # namedtuples => columns names would need to be valid python identifiers
        # and there would be a limit of 255 columns
        row_vals = dict(zip(columns,row_tuple)) # Maps col names to row values
        # Generate the value of each grid parameter for this row (in order)
        row_p_vals = ( row_vals[p] for p in Params.names )
        # List the grid indices associated with the param values for this row
        row_p_inds = [Raw_grids.par_indices[j][v] for j,v in enumerate(row_p_vals)]
        for line in lines_list: # Iterate emission lines
            # Write the line flux value for this gridpoint into the correct
            # location in the flux array for this line:
            Raw_grids.grids[line][tuple(row_p_inds)] = row_vals[line]

    arr_n_bytes = Raw_grids.grids[lines_list[0]].nbytes
    n_lines = len(lines_list)
    print( """Number of bytes in raw grid flux arrays: {0} for 1 emission line, 
    {1} total for all {2} lines""".format( arr_n_bytes, arr_n_bytes*n_lines,
                                                                    n_lines ) )

    #--------------------------------------------------------------------------
    # Interpolate model grids to a higher resolution and even spacing...
    print("Interpolating flux arrays for the model grids...")
    # # # A list of all parameter value combinations in the
    # # # interpolated grid in the form of a numpy array:
    # # param_combos = np.array( list(
    # #         itertools.product( *param_val_arrs_interp ) ) )
    # # # A list of all index combinations for the
    # # # interpolated grid, corresponding to param_combos:
    # # param_index_combos = np.array( list(
    # #         itertools.product( *[np.arange(n) for n in Interpd_grids.shape] ) ) )
    # Generate a tuple containing an index array for each grid dimension,
    # with each combination of indices (e.g. from the 7th entry in each
    # index array) correspond to a param_combos row (e.g. the 7th):
    
    # param_fancy_index = tuple(
    #         [ param_index_combos[:,i] for i in np.arange(Params.n_params) ] )
    # This will be used to take advantage of numpy's fancy indexing capability.

    for emission_line in lines_list:
        pass
        # # Create new (emission_line, flux_array) item in dictionary of interpolated grid arrays:
        # Interpd_grids.grids[emission_line] = np.zeros( Interpd_grids.shape )
        

        # Function here!
        # # Create function for carrying out the interpolation:
        # interp_fn = siRGI(tuple(Raw_grids.val_arrs),
        #                   Raw_grids.grids[emission_line], method="linear")
        
    #     # Fill the interpolated fluxes into the final grid structure, using "fancy indexing":
    #     Interpd_grids.grids[emission_line][param_fancy_index] = interp_fn( param_combos )
    # print( "An interpolated grid flux array for a single emission line is " + 
    #        str(Interpd_grids.grids[lines_list[0]].nbytes) + " bytes" )

    #--------------------------------------------------------------------------
    # Combine and return results...
    container1 = Bigelm_container()  # Initialise container object
    container1.Params = Params
    container1.Raw_grids = Raw_grids
    container1.flux_interpolators = setup_interpolators(Raw_grids)

    return container1




#============================================================================
def setup_interpolators(Raw_grids):
    """
    
    I'm jumping through hoops here becuase I can't find an ND spline
    interpolator that I'm happy with here...
    """

    p_index_interpolators = []
    for a in Raw_grids.val_arrs: # Setup linear interpolator for each param
        interpolator = interp1d(x=a, y=np.arange(len(a)), bounds_error=True)
        p_index_interpolators.append(interpolator)
    # So p_index_interpolators[j][f] will give the interpolated "index"
    # corresponding to the value f along the j axis (i.e. to parameter j having
    # value f).  We're converting the actual value of the parameter to a "pixel"
    # coordinate.

    line_interpolators = {}
    for line, flux_grid in Raw_grids.grids.items():

        def line_interpolator(p_vector):
            """
            Function for interpolating the value of an emission line, given a
            list of parameter values.
            """
            # http://stackoverflow.com/questions/6238250/multivariate-spline-interpolation-in-python-scipy
            # Firstly convert the parameter values to pixel coordinates:
            coords = [p_index_interpolators[i][f] for i,f in enumerate(p_vector)]
            coords = np.array(coords)[:,np.newaxis] # Need transpose
            # Now interpolate in the flux grid using the pixel coordinates:
            flux = ndimage.map_coordinates(flux_grid, coords, order=3)
            return flux[0] # Return as a float, not a numpy array
            # In the coords array, each column is a point to be interpolated/
            # Here we have only one column.
            # We use 3rd-order (cubic) spline interpolation.
            # We don't need to worry about interpolating outside the grid,
            # since the p_index_interpolators would have thrown an error

            # Lingering questions:
            # - Could there be an issue with the spline interpolation
            # returning negative flux values?
            # - Is this too slow because the array is being copied in memory
            # on each interpolation?
            # - Does this naive spline interpolation (not Akima spline) behave
            # poorly with "outliers" in the model grid data?

        line_interpolators[line] = line_interpolator

    return line_interpolators


