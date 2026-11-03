# CLAW Raspberry Pi 5

This repository contains the Raspberry Pi software for CLAW, a powered hand and wrist orthosis project. It includes tools for training and evaluating EMG gesture classifiers, plus a Bluetooth interface for testing the calibration workflow from a phone.

## File structure

```text
Pi-5-Stuff/
├── CLAW_Link/                 # Bluetooth server and phone interface
│   ├── pi/                    # Python servers for the Raspberry Pi
│   └── web/                   # Phone interface and connection test pages
├── ml_classification/         # EMG model training and evaluation
│   ├── EMG_data/              # Gesture and Mendeley datasets
│   ├── lda/                   # Linear discriminant analysis
│   ├── random_forest/         # Random forest
│   └── svm/                   # Support vector machine
└── Pi5-Connection-Guide.md    # How to connect to the Pi
```

- [Connect to the Pi](Pi5-Connection-Guide.md)
- [Run the phone calibration demo](CLAW_Link/README.md)
- [Train and compare EMG classifiers](ml_classification/README.md)
