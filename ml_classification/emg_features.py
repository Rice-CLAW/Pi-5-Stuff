import numpy as np

def baseline(window):
    """MAV, RMS, standard deviation, mean waveform length, in that order."""
    return np.concatenate([np.abs(window).mean(axis=0),
                           np.sqrt((window ** 2).mean(axis=0)),
                           window.std(axis=0),
                           np.abs(np.diff(window, axis=0)).mean(axis=0)])

def extract(w, name, threshold):
    if name == 'baseline':
        return baseline(w)
    # Thresholds are fixed in the CSV's raw amplitude units, not inferred from test data.
    d = np.diff(w, axis=0)
    mav = np.abs(w).mean(axis=0)
    wl = np.abs(d).sum(axis=0)
    zc = (((w[:-1] * w[1:]) < 0) & (np.abs(d) >= threshold)).sum(axis=0)
    a, b = w[1:-1] - w[:-2], w[1:-1] - w[2:]
    ssc = ((a * b > 0) & ((np.abs(a) >= threshold) | (np.abs(b) >= threshold))).sum(axis=0)
    if name == 'hudgins':
        return np.concatenate([mav, wl, zc, ssc])
    # AR(4): centered, channel-scaled least squares, x[t] = sum(a[k]*x[t-k]).
    v = w - w.mean(axis=0)
    v = v / np.maximum(v.std(axis=0), 1e-8)
    ars = []
    for c in range(8):
        predictors = np.column_stack([v[4-k:len(v)-k,c] for k in range(1,5)])
        ars.extend(np.linalg.lstsq(predictors, v[4:,c], rcond=None)[0])
    if name == 'tdar':
        return np.concatenate([mav, wl, zc, ssc, ars])
    # Engineering variant: reduce overall contraction-strength scaling while retaining
    # relative activation across channels. Not claimed as a published feature set.
    rms = np.sqrt((w*w).mean(axis=0))
    return np.concatenate([mav / max(mav.sum(),1e-8),
                           wl / max(wl.sum(),1e-8),
                           zc / (len(w)-1), ssc / (len(w)-2), ars,
                           [np.log1p(rms.mean())]])
