VARIABLE_MAP = {
    "mslp": {"var": "prmslmsl"},
    "rainrate": {"var": "apcpsfc"},
    "relhum": {"var": "rh2msfc"},
    "temp": {"var": "tmpsfc"},
    # 10 m wind for MSLP+wind composite (m/s in GRIB)
    "mslp_wind": {"u": "ugrd10m", "v": "vgrd10m"},
    # Rainfall shaded under 700 hPa moisture contours
    "rain_rh700": {"rain": "apcpsfc", "rh": "rh700mb"},
    "source": "GFS 0.25deg",
}
