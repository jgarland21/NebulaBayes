from __future__ import print_function, division
from collections import OrderedDict as OD
import numpy as np  # Core numerical library
import pandas as pd # For tables ("DataFrame"s)
from . import NB1_Process_grids
from . import NB3_Bayes
from .NB4_Plotting import ND_PDF_Plotter


"""
NebulaBayes
Adam D. Thomas
Research School of Astronomy and Astrophysics
Australian National University
2015 - 2017

The NB_Model class in this module is the entry point for performing Bayesian
parameter estimation.  The data are a set of emission line flux measurements
with associated errors.  The model is a photoionisation model, varied in a grid
over n=2 or more parameters, input as n-dimensional grids of fluxes for each
emission line.  The model is for an HII region or AGN Narrow Line Region, for
example.
The measured and modelled emission line fluxes are compared to calculate a
"likelihood" probability distribution, before Bayes' Theorem is applied to
produce an n-dimensional "posterior" probability distribution for the values of
the parameters.  The parameter values are estimated from 1D marginalised
posteriors.

NebulaBayes is heavily based on IZI (Blanc+2015).
"""



class NB_Model(object):
    """
    Primary class for working with NebulaBayes.  To use, initialise a class
    instance with a model grid and then call the instance one or more times to
    run Bayesian parameter estimation.
    """

    def __init__(self, grid_table, grid_params, lines_list, **kwargs):
        """
        Initialise an instance of the NB_Model class.

        Parameters
        ----------
        grid_table : str or pandas DataFrame
            The table of photoionisation model grid fluxes, given as either the
            filename of a csv, FITS (.fits) or compressed FITS (fits.gz) file,
            or a pandas DataFrame instance.
            Each gridpoint (point in parameter space) is a row in the table.
            There is a column for each parameter; the location of a gridpoint is
            defined by these parameter values.  There is a column of fluxes for
            each modelled emission line. 
            No assumptions are made about the order of the gridpoints (rows) or
            the order of the columns in the table.  Unnecessary columns will be
            ignored, but the number of rows must be exact - the grid must be
            rectangular with exactly one gridpoint for every possible
            combination of included parameter values.  Note that the sampling
            of a parameter (spacing of gridpoints) may be uneven.
            Model fluxes will be normalised by NebulaBayes (see "norm_line"
            parameter to __call__ below).  Any non-finite fluxes (e.g. nans)
            will be set to zero.
        grid_params : list of strings
            The names of the grid parameters.  This list sets the order of the
            grid dimensions, i.e. the order in which arrays in NebulaBayes will
            be indexed.  Each name must match a column header in the grid_table.
        lines_list : list of strings
            The emission lines to use in this NB_Model instance.  Each name must
            match a column name in grid_table.  Unused lines may be excluded.

        Optional parameters
        -------------------
        interpd_grid_shape : tuple of integers, optional
            The size of each dimension of the interpolated flux grids, default
            (15,)*ndim.  The order of the integers corresponds to the order of
            parameters in grid_params.  These values have a major impact on the
            speed of the grid interpolation.
        grid_error : float between 0 and 1, optional
            The systematic relative error on grid fluxes, as a linear
            proportion.  Default is 0.35 (average of errors of 0.15 dex above
            and below).
        """
        # Initialise and do some checks...
        print("Initialising NebulaBayes model...")
        n_params = len(grid_params)
        if len(set(grid_params)) != n_params: # Parameter names non-unique?
            raise ValueError("grid_params are not all unique")
        if len(set(lines_list)) != len(lines_list): # Line names non-unique?
            raise ValueError("Line names in lines_list are not all unique")
        if len(lines_list) < 2:
            raise ValueError("At least two modelled lines required (one is for "
                                                                 "normalising)")

        # Interpolated grid shape (default: [15, 15, ..., 15])
        interpd_grid_shape = kwargs.pop("interpd_grid_shape", [15] * n_params)
        if len(interpd_grid_shape) != n_params:
            raise ValueError("interpd_grid_shape has wrong length: needs "
                             "exactly one integer for each parameter")

        grid_rel_error = kwargs.pop("grid_error", 0.35) # Default: 0.35
        if not 0 < grid_rel_error < 1:
            raise ValueError("grid_error must be between 0 and 1")

        # Are there any remaining keyword arguments that weren't used?
        if len(kwargs) > 0:
            raise ValueError("Unknown keyword argument(s) " +
                                      ", ".join(str(k) for k in kwargs.keys()) )

        # Call grid initialisation:
        Raw_grids, Interpd_grids = NB1_Process_grids.initialise_grids(grid_table,
                                    grid_params, lines_list, interpd_grid_shape)
        Raw_grids.grid_rel_error = grid_rel_error
        Interpd_grids.grid_rel_error = grid_rel_error
        self.Raw_grids = Raw_grids
        self.Interpd_grids = Interpd_grids
        # Creat a ND_PDF_Plotter instance to plot corner plots
        self.Plotter = ND_PDF_Plotter(Raw_grids.paramName2paramValueArr)
        # The ND_PDF_Plotter instance will be an attribute on both this NB_Model
        # instance and on all "NB_Result" instances created later.



    def __call__(self, obs_fluxes, obs_flux_errors, obs_emission_lines, **kwargs):
        """
        Run NebulaBayes Bayesian parameter estimation using the interpolated
        grids stored in this NB_Model object.
        
        Parameters
        ----------
        obs_fluxes : list of floats
            The observed emission-line fluxes
        obs_flux_errors : list of floats
            The corresponding measurement errors
        obs_emission_lines : list of str
            The corresponding emission line names, matching names in the header
            of the input grid flux table
        
        Optional parameters - for parameter estimation
        ----------------------------------------------
        norm_line : str
            Observed and grid fluxes will be normalised to this emission line.
            Because the likelihood calculation will use fluxes that are actually
            ratios to this line, the choice may affect parameter estimation.
            Where the model grid for norm_line has value zero, the normalised
            grids are set to zero.  Default: "Hbeta"
        deredden : bool
            De-redden observed fluxes to match the Balmer decrement at each
            interpolated grid point?  Only supported for norm_line = "Hbeta".
            Default: False
        obs_wavelengths : list of floats
            If deredden=True, you must also supply a list of wavelengths
            (Angstroems) associated with obs_fluxes.  Default: None
        prior : list of ("line1","line2") tuples, or "Uniform", or a callable
            The prior to use when calculating the posterior.  Either a user-
            defined function, the string "Uniform", or a list of length at least
            one. Entries in the list are tuples such as ("SII6716","SII6731") to
            specify a line ratio to use as a prior.  The listed line-ratio
            priors will all be multiplied together (weighted equally) and then
            normalised before being used in Bayes' Theorem.  See the code file
            "src/NB2_Prior.py" for the details of the prior calculations,
            including to see the required inputs and output for a user-defined
            prior function.  Default: "Uniform"

        Optional parameters - outputs
        -----------------------------
        Provide a value for a keyword to produce the corresponding output.
        param_display_names : dict
            Parameter display names for grid parameters, for plotting purposes.
            The dictionary keys are parameter names from the grid file, and the
            corresponding values are the "display" names.  The display names can
            include markup (e.g. r"$\alpha$") in order to include e.g. Greek
            letters.  Not all of the grid parameters need to be in
            param_display_names; raw parameter names will be used by default.
        posterior_plot : str
            A filename for a results image of 2D and 1D marginalised posterior
            PDFs. The image file type is specified by the file extension.
        prior_plot : str
            As for posterior_plot but for the prior
        likelihood_plot : str
            As for posterior_plot but for the likelihood
        estimate_table : str
            A filename for a csv file containing Bayesian parameter estimates
            for the grid parameters
        best_model_table : str
            A filename for a csv file which will compare observed and model
            fluxes at the point defined by the Bayesian parameter estimates.
        table_on_plots : bool
            Include a "best model" flux comparison table on the 'corner' plots
            (i.e. plots of 1D and 2D marginalised PDFs)?  Default: True
        line_plot_dir : str
            A directory; 'corner' plots showing the nD PDFs for each line (the
            PDFs which contribute to the likelihood) are saved here.  Saving
            these plots is quite slow.  The plots show the constraints provided
            by each line.

        Returns
        -------
        NB_Result
            Object (defined in src/NB3_Bayes.py), which contains the data
            relevant to the Bayesian parameter estimation.
        """
        print("Running NebulaBayes...")

        if "norm_line" not in kwargs and "Hbeta" not in obs_emission_lines:
            raise ValueError("Can't normalise by default line 'Hbeta': not "
                             "found in obs_emission_lines line names.  Maybe "
                             "set keyword 'norm_line' to another line?")
        norm_line = kwargs.pop("norm_line", "Hbeta") # Default "Hbeta"
        deredden = kwargs.pop("deredden", False) # Default False
        assert isinstance(deredden, bool)
        if deredden and not all((l in obs_emission_lines) for l in ["Halpha","Hbeta"]):
            raise ValueError("'Halpha' and 'Hbeta' must be provided for deredden=True")
        obs_wavelengths = kwargs.pop("obs_wavelengths", None) # Default None
        if deredden and (obs_wavelengths is None):
            raise ValueError("Must supply obs_wavelengths for deredden=True")
        if deredden is False and (obs_wavelengths is not None):
            pass # obs_wavelengths is unnecessary but will be checked anyway.
        # Process the input observed data; DF_obs is a pandas DataFrame table
        # where the emission line names index the rows:
        DF_obs = _process_observed_data(obs_fluxes, obs_flux_errors,
                       obs_emission_lines, obs_wavelengths, norm_line=norm_line)
        for line in DF_obs.index:  # Check observed emission lines are in grid
            if line not in self.Interpd_grids.grids["No_norm"]:
                raise ValueError("The line {0}".format(line) + 
                                 " was not previously loaded from grid table")

        input_prior = kwargs.pop("prior", "Uniform") # Default "Uniform"

        #----------------------------------------------------------------------
        # Handle options for NebulaBayes outputs:
        # Determine the parameter display names to use for plotting:
        param_list = list(self.Interpd_grids.param_names)
        param_display_names = OD(zip(param_list, param_list)) # Default; ordered
        if "param_display_names" in kwargs:
            custom_display_names = kwargs.pop("param_display_names")
            if not isinstance(custom_display_names, dict):
                raise TypeError("param_display_names must be a dict")
            for p, custom_name in custom_display_names.items():
                if p not in param_list:
                    raise ValueError("Unknown parameter in param_display_names")
                param_display_names[p] = custom_name  # Override default
        self.Interpd_grids.param_display_names = list(param_display_names.values())
        # Include text "best model" table on posterior corner plots?
        table_on_plots = kwargs.pop("table_on_plots", True) # Default True
        # Handle directory and file names for the outputs:
        output_locations = {}
        for key in ["likelihood_plot", "prior_plot", "posterior_plot",
                    "line_plot_dir", "estimate_table", "best_model_table"]:
            output_locations[key] = kwargs.pop(key, None)
            # Default None means "Don't produce the relevant output"

        # Any remaining keyword arguments that weren't used?
        if len(kwargs) > 0:
            raise ValueError("Unknown keyword argument(s): " +
                             ", ".join("'{0}'".format(k) for k in kwargs.keys()))

        #----------------------------------------------------------------------
        # Create a "NB_Result" object instance, which involves calculating
        # the prior, likelihood and posterior, along with parameter estimates:
        Result = NB3_Bayes.NB_Result(self.Interpd_grids, DF_obs, self.Plotter,
                                     deredden=deredden, input_prior=input_prior,
                                line_plot_dir=output_locations["line_plot_dir"])

        #----------------------------------------------------------------------
        # Write out the results
        table_map = { "estimate_table"   : Result.Posterior.DF_estimates,
                      "best_model_table" : Result.Posterior.DF_best }
        for table_name, DF in table_map.items():
            out_table_name = output_locations[table_name]
            if out_table_name is not None:
                DF.to_csv(out_table_name, index=True, float_format="%.5f")

        # Plot corner plots if requested:
        ndpdf_map = { "likelihood" : Result.Likelihood, "prior" : Result.Prior,
                      "posterior"  : Result.Posterior }
        for ndpdf_name, NB_nd_pdf in ndpdf_map.items():
            out_image_name = output_locations[ndpdf_name + "_plot"]
            if out_image_name is None:
                continue  # Only do plotting if an image name was specified
            plot_anno = None
            if table_on_plots is True: # Include a fixed-width text table on image
                pd.set_option("display.precision", 4)
                plot_anno = ("Observed fluxes vs. model fluxes at the gridpoint of"
                             "\nparameter best estimates in the "+ndpdf_name+"\n")
                plot_anno += str(NB_nd_pdf.DF_best) + "\n\n"
                plot_anno += r"$\chi^2_r$ = {0:.1f}".format(NB_nd_pdf.chi2)                
            NB_nd_pdf.Grid_spec.param_display_names = list(
                                                   param_display_names.values())
            print("Plotting corner plot for the", ndpdf_name, "...")
            self.Plotter(NB_nd_pdf, out_image_name, plot_anno)

        print("NebulaBayes finished.")
        return Result



def _process_observed_data(obs_fluxes, obs_flux_errors, obs_emission_lines,
                                                    obs_wavelengths, norm_line):
    """
    Error-check the input observed emission line data, form it into a pandas
    DataFrame table, and normalise by the specified line.
    """
    obs_fluxes = np.asarray(obs_fluxes, dtype=float) # Ensure numpy array
    obs_flux_errors = np.asarray(obs_flux_errors, dtype=float)
    # Check measured data inputs:
    n_measured = len(obs_emission_lines)
    if (obs_fluxes.size != n_measured) or (obs_flux_errors.size != n_measured):    
        raise ValueError("Inputs obs_fluxes, obs_flux_errors and " 
                         "obs_emission_lines don't all have the same length.")
    if len(set(obs_emission_lines)) != n_measured:
        raise ValueError("obs_emission_lines line names are not all unique")
    if n_measured < 2:
        raise ValueError("At least two observed lines are required (one is for "
                                                                 "normalising)")
    if obs_wavelengths is not None:
        obs_wavelengths = np.asarray(obs_wavelengths, dtype=float)
        if obs_wavelengths.size != n_measured:
            raise ValueError("obs_wavelengths must have same length as obs_fluxes")
        # Check input wavelengths:
        if np.any(~np.isfinite(obs_wavelengths)): # Any non-finite?
            raise ValueError("The wavelength for an emission line isn't finite.")
        if np.any(obs_wavelengths <= 0): # Any non-positive?
            raise ValueError("The wavelength for an emission line not positive.")

    # Check input measured fluxes:
    if np.any(~np.isfinite(obs_fluxes)): # Any non-finite?
        raise ValueError("The measured flux for an emission line isn't finite.")
    if np.any(obs_fluxes < 0): # Any negative?
        raise ValueError("The measured flux for an emission line is negative.")
    
    # Check input measured flux errors:
    if np.any(~np.isfinite(obs_flux_errors)): # Any non-finite?
        raise ValueError("The flux error for an emission line isn't finite.")
    if np.any(obs_flux_errors <= 0): # All positive?
        raise ValueError("The flux error for an emission line is not positive.")

    # Form the data from the observations into a pandas DataFrame table.
    obs_dict = OD([("Line", obs_emission_lines)])
    if obs_wavelengths is not None: # Leave out of DataFrame if not provided
        obs_dict["Wavelength"] = obs_wavelengths
    obs_dict["Flux"] = obs_fluxes
    obs_dict["Flux_err"] = obs_flux_errors
    DF_obs = pd.DataFrame(obs_dict)
    DF_obs.set_index("Line", inplace=True) # Row index is the emission line name

    # Normalise the fluxes:
    if norm_line not in DF_obs.index.values:
        raise ValueError("norm_line '{0}' not found in input line names".format(
                                                                     norm_line))
    norm_flux = DF_obs.loc[norm_line, "Flux"] * 1.0
    if norm_flux == 0:
        raise ValueError("The obs flux for norm_line ({0}) is 0".format(norm_line))
    DF_obs["Flux"] = DF_obs["Flux"].values / norm_flux
    DF_obs["Flux_err"] = DF_obs["Flux_err"].values / norm_flux
    assert np.isclose(DF_obs.loc[norm_line, "Flux"], 1.0) # Ensure normalised
    DF_obs.norm_line = norm_line  # Store as attribute on DataFrame
    # Note that storing metadata on DataFrames isn't trivial - we may lose the
    # "norm_line" attribute if we do some common operations on DF_obs.

    return DF_obs


