"""
This is a module to predict the background levels for JWST observations, 
for use in proposal planning. The code is based on subset of a larger code (JRR_Code/read_JWST_bkg.py),
written by Jane Rigby, Jane.Rigby@nasa.gov.

It accesses a precompiled background cache prepared by STScI, to do the following:
- Plot the background versus calendar day.
- Compute the number of days per year that a target is observable at low background,
  for a given wavelength and a selectable threshold.

 
The background cache was prepared by Wayne Kinzel (STScI).  

Software is provided as-is, with no warranty. Use the latest versions of APT and ETC to confirm 
the observability of any JWST targets. 
 
"""

import os
import struct
import urllib
import healpy
import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d

__version__ = 1.0

class background():
    '''
    Main background class. It is initialized with all background data for a specific
    position (RA, DEC). The wavelength at which the bathtub curve is calculated 
    can be updated as needed.
    
    Parameters
    ----------
    ra: float
    dec: float
    wavelength: float
    thresh: float
    
    Attributes
    ----------
    bkg_data: dict
        Contains all the data for the background for the input (RA,DEC) position 
    bathtub: 
        Contains the (RA,DEC) background information as a function of calendar day, interpolated at wavelength
    '''
    def __init__(self, ra, dec, wavelength, thresh=1.21):
        # global attributes
        self.cache_url = 'http://www.stsci.edu/~pontoppi/straylight_1.0/sl_cache/' # Path to the online location of the background cache
        self.local_path = os.path.join(os.path.dirname(__file__),'refdata')
        self.wave_file = 'std_spectrum_wavelengths.txt' # The wavelength grid of the background cache
        self.thermal_file = 'thermal_curve_jwst_jrigby_cchen_1.1a.csv' # The constant (not time variable) thermal self-emission curve
        self.nside = 128  # Healpy parameter, from generate_backgroundmodel_cache.c .  
        self.wave_array,self.thermal_bg = self.read_static_data()
        self.sl_nwave = self.wave_array.size  # should be 108.  Size of wavelength array
        
        # input parameters
        self.ra = ra
        self.dec = dec
        self.wavelength = wavelength
        self.thresh = 1.21

        # Load variable content
        self.cache_file = self.myfile_from_healpix(ra, dec)
        self.bkg_data = self.read_bkg_data(self.cache_file)
        
        # Interpolate bathtub curve and package it    
        self.make_bathtub(wavelength)


    def myfile_from_healpix(self, ra, dec):
        healpix = healpy.pixelfunc.ang2pix(self.nside, ra, dec, nest=False, lonlat=True)  # old versions of healpy don't have lonlat
        file = str(healpix)[0:4] + "/sl_pix_" + str(healpix) + ".bin"
        return file

    def read_static_data(self):
        abs_wave_file = os.path.join(self.local_path, self.wave_file)  # Standard wavelength array.  Should be SL_NWave=108 long
        wave_array = np.loadtxt(abs_wave_file)    
                
        thermal = np.genfromtxt(os.path.join(self.local_path, self.thermal_file), delimiter=',')
        thermal_int = self.interpolate_spec(thermal[:, 0], thermal[:,1],  wave_array, fill=0.0)  # interpolate to same wavelength_array as others.
        
        return wave_array,thermal_int

    def read_bkg_data(self, cache_file, verbose=False):
        """
        Method for reading one JWST background file, and parsing it.    
        
        Schema of each binary file in the cache:
        ----------------------------------------
        JRR verified the schema against the source code, generate_stray_light_with_threads.c. 
        The cache uses a Healpix RING tesselation, with NSIDE=128.  Every point on the sky 
        (tesselated tile) corresponds to one binary file, whose name includes its healpix 
        pixel number, in a directory corresponding to the first 4 digits of the healpix number.  

        - double RA
        - double DEC
        - double pos[3]
        - double nonzodi_bg[SL_NWAVE]
        - int[366] date_map  : This maps dates to indices.  NOTE: There are 366 days, not 365!
        - for each day in FOR:
            - double zodi_bg[SL_NWAVE]
            - double stray_light_bg[SL_NWAVE]
        
        parameters
        ----------
        cache_file: string
        
        attributes
        ----------
        
        """

        # Read the background cache version
        version_file = urllib.request.urlopen(self.cache_url + 'VERSION')
        self.cache_version = version_file.readlines()[0].decode('utf-8')[:-1]
        
        # Read the background file via http 
        try:
            # Python 3
            sbet_file = urllib.request.urlopen(self.cache_url + cache_file)
        except:
            # Python 2
            sbet_file = urllib.urlopen(self.cache_url + cache_file)

        sbet_data = sbet_file.read()
        
        # Unpack the constant first part
        if verbose: 
            print("File has", len(sbet_data), "bytes, which is", len(sbet_data)/8., "doubles")
            
        size_calendar = struct.calcsize("366i") # bytes, not doubles
        partA = struct.unpack(str(5 + self.sl_nwave)+'d', sbet_data[0: (5 + self.sl_nwave)*8])
        ra = partA[0]
        dec = partA[1]
        pos = partA[2:5]
        nonzodi_bg = np.array(partA[5:5+self.sl_nwave])

        # Unpack the calendar dates      # code goes from 0 to 365 days.
        date_map = np.array(struct.unpack('366i', sbet_data[(5 + self.sl_nwave)*8  : (5 + self.sl_nwave)*8 + size_calendar]))
        if verbose: 
            print("Out of", len(date_map), "days, these many are legal:", np.sum(date_map >=0))

        calendar = np.where(date_map >=0)[0]
        # So, the index dd in zodi_bg[dd, : ]  corresponds to the calendar day lookup[dd]
        Ndays = len(calendar) 
        if verbose: 
            print(len(date_map), Ndays)

        # Unpack part B, the time-variable part
        zodi_bg        = np.zeros((Ndays,self.sl_nwave))
        stray_light_bg = np.zeros((Ndays,self.sl_nwave))
        perday = self.sl_nwave*2
        partB= struct.unpack(str((len(calendar))*self.sl_nwave*2)+'d', sbet_data[perday*Ndays*-8 : ])

        for dd in range(0, int(Ndays)):
            br1 = dd*perday
            br2 = br1 + self.sl_nwave
            br3 = br2 + self.sl_nwave
            zodi_bg[dd, ] = partB[br1 : br2]
            stray_light_bg[dd, ] = partB[br2 : br3]

        # Expand static background components to the same shape as zodi_bg
        total_bg = np.tile(nonzodi_bg + self.thermal_bg,(Ndays,1)) + stray_light_bg + zodi_bg

        return {'calendar':calendar, 'ra':ra, 'dec':dec, 'pos':pos, 'wave_array':self.wave_array, 'nonzodi_bg':nonzodi_bg, 
                'thermal_bg':self.thermal_bg, 'zodi_bg':zodi_bg, 'stray_light_bg':stray_light_bg, 'total_bg':total_bg}  #pack it up as a dict

    def make_bathtub(self, wavelength):
        """
        This method interpolates a bathtub curve at a given wavelength.
        It also uses the threshold fraction ("thresh") above the minimum background, to calculate number of good days.
        
        parameters
        ----------
        wavelength: float
        
        """
        
        self.wavelength = wavelength
        wave_array = self.bkg_data['wave_array']

        total_thiswave = (interp1d(wave_array, self.bkg_data['total_bg'], bounds_error=True))(wavelength)
        stray_thiswave = (interp1d(wave_array, self.bkg_data['stray_light_bg'], bounds_error=True))(wavelength)
        zodi_thiswave = (interp1d(wave_array, self.bkg_data['zodi_bg'], bounds_error=True))(wavelength)
        thermal_thiswave = (interp1d(wave_array, self.bkg_data['thermal_bg'], bounds_error=True))(wavelength)
        nonzodi_thiswave = (interp1d(wave_array, self.bkg_data['nonzodi_bg'], bounds_error=True))(wavelength)
            
        themin = np.min(total_thiswave)
        good_days =  int(np.sum(total_thiswave < themin * self.thresh)*1.0)
        
        self.bathtub = {'wavelength':wavelength,'themin':themin,'good_days':good_days,
                        'total_thiswave':total_thiswave,'stray_thiswave':stray_thiswave,'zodi_thiswave':zodi_thiswave,
                        'thermal_thiswave':thermal_thiswave,'nonzodi_thiswave':nonzodi_thiswave}

    def interpolate_spec(self, wave, specin, new_wave, fill=np.nan):
        f = interp1d(wave, specin, bounds_error=False, fill_value=fill)  # With these settings, writes NaN to extrapolated regions
        new_spec = f(new_wave)
        return new_spec
                    
    def plot_background(self, thisday=-99, fontsize=16, xrange=(0.6,30), yrange=(1e-4,1e4)):
        wave_array = self.bkg_data['wave_array']
        calendar = self.bkg_data['calendar']
        
        plt.clf()
        if thisday not in calendar: 
            thisday = (np.abs(calendar-np.mean(calendar))).argmin() # plot the middle of the calendar
            
        plt.plot(wave_array, self.bkg_data['nonzodi_bg'], label="ISM")
        plt.plot(wave_array, self.bkg_data['zodi_bg'][thisday, :], label="Zodi")
        plt.plot(wave_array, self.bkg_data['stray_light_bg'][thisday, :], label="Stray light")
        plt.plot(wave_array, self.bkg_data['thermal_bg'], label = "Thermal")
        plt.plot(wave_array, self.bkg_data['total_bg'][thisday, :], label = "Total", color='black', lw=3)
        plt.xlim(xrange)
        plt.ylim(yrange)
        
        plt.xlabel("wavelength (micron)", fontsize=fontsize)
        plt.ylabel("Equivalent in-field radiance (MJy/sr)", fontsize=fontsize)
        plt.title("Background for calendar day "+str(thisday))
        plt.legend()
        plt.yscale('log')
        plt.show()

    def plot_bathtub(self,showthresh=True, showplot=False, showsubbkgs=False, showannotate=True, title=False, label=False):
        
        bathtub = self.bathtub # local link
        
        calendar = self.bkg_data['calendar']
        plt.scatter(calendar, bathtub['total_thiswave'], s=20, label=label)
        plt.xlabel("Day of the year", fontsize=12)
        plt.xlim(0,366)
            
        if showannotate:
            annotation = str(bathtub['good_days']) + " good days out of " + str(calendar.size) + \
                         " days observable, for thresh " + str(self.thresh)
            plt.annotate(annotation, (0.05,0.05), xycoords="axes fraction", fontsize=10)
            plt.ylabel("bkg at " + str(bathtub['wavelength']) + " um (MJy/sr)", fontsize=12)
        else: 
            plt.ylabel("bkg (MJy/SR)", fontsize=fontsize)

        if not label:
            label="Total " + str(bathtub['wavelength']) + " micron"

        if showsubbkgs :
            plt.scatter(calendar, bathtub['zodi_thiswave'], s=20, label="Zodiacal")
            plt.scatter(calendar, bathtub['stray_thiswave'], s=20, label="Stray light")
            plt.scatter(calendar, bathtub['nonzodi_thiswave']*np.ones_like(calendar), s=20, label="ISM+CIB")
            plt.scatter(calendar, bathtub['thermal_thiswave']*np.ones_like(calendar), s=20, label="Thermal")
            plt.legend(fontsize=10, frameon=False, labelspacing=0)
            plt.grid()
            plt.locator_params(axis='x', nbins=10)
            plt.locator_params(axis='y', nbins=10)

        if showthresh: 
            percentiles = (bathtub['themin'], bathtub['themin']*self.thresh)
            plt.hlines(percentiles, 0, 365, color='black')
                    
        if title: 
            plt.title(title)
    
        plt.show()
        
    def write2file(self,outfile='background_versus_day.txt'):
        f = open(outfile,'w')
        header_text = ["# Output of JWST_backgrounds version " + str(__version__) + "\n",
                       "# background cache version " + str(self.cache_version) + '\n',
                       "\n"
                       "# for RA="+str(self.ra) + ", DEC=" + str(self.dec) + " at wavelength=" + str(self.wavelength) + " micron \n",
                       "# Columns: \n",
                       "# - Calendar day (Jan1=0) \n",
                       "# - Total background (MegaJanskies per sterradian)\n"] 
        for line in header_text:
            f.write(line)               
        
        for i,calendar_day in enumerate(self.bkg_data['calendar']):
            f.write('{0}    {1:5.4f}'.format(calendar_day, self.bathtub['total_thiswave'][i])+'\n')
        
        f.close()
            
def get_background(ra, dec, wavelength, thresh=1.21, plot_background=True, plot_bathtub=True, thisday=-99,
                   showsubbkgs=False, write_bathtub=True, outfile='background_versus_day.txt'):
    """
    This is the main method, which serves as a wrapper to get the background data and create plots and outputs with one command.
    
    Parameters
    ----------
    ra: float
        Right ascension in decimal degrees
    dec: float 
        Declination in decimal degrees
    wavelength: float 
        Wavelength at which the bathtub curve is calculated, in micron
    thresh: float
        the background threshold, relative to the minimum.  Default=1.21, which corresponds to 10% above the minimum background noise.
    plot_background: bool 
        whether to plot the background spectrum (and its components) for this day.
    thisday: int
        calendar day to use for plot_spec.  If not given, will use the average of visible calendar days.
    plot_days: bool
        whether to show the plot of background at wavelength_input versus calendar days
    showsubbkgs: bool
        whether to show the components of the background in the plot_days plot.
    write_bathtub: bool
        whether to print the background levels that are plotted in plot_days to an outfile
    outfile:     output filename

    """

    bkg = background(ra,dec,wavelength, thresh=thresh)
    
    print("RESULTS:  These coordinates are observable by JWST", len(bkg.bkg_data['calendar']), "days per year.")
    print("RESULTS:  For", bkg.bathtub['good_days'], "of those days, the background is <", thresh, "times the minimum, at wavelength", wavelength, "micron")
    
    bkg.plot_background()
    bkg.plot_bathtub()
    bkg.write2file()

    