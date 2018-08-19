# Python Plugin Engine
The plugin engine used for running python plugins in https://imjoy.io

## Installation
  * Download and install [Anaconda](https://www.anaconda.com/download/) or [Miniconda](https://conda.io/miniconda.html) (Python3.6+ version is preferred)
  * Start a **Terminal**(Mac and Linux) or **Anaconda Prompt**(Windows), then run the following command:

    ```conda -V && pip install -U git+https://github.com/oeway/ImJoy-Python#egg=imjoy```
  * If you encountered any error related to `git` or `pip`, try to run : `conda install -y git pip` before the above command. (Otherwise, please check **FAQs**.)

## Usage
  * Run `python -m imjoy` in a **Terminal** or **Anaconda Prompt**, and keep the window running.
  * Go to https://imjoy.io, connect to the plugin engine. For the first time, you will be asked to fill a token generated by the plugin engine from the previous step.
  * Now you can start to use plugins written in Python.

## Going offline
  ImJoy is designed to be offline ready, the engine serve a mirror site of ImJoy.IO locally. In order to do that, you need to first start the Python Plugin Engine by adding `--offline` in the command line:
  ```
  python -m imjoy --offline
  ```
Once it's done, you will be able to access your personal ImJoy web app through: [http://localhost:8080](http://localhost:8080).

Also notice that, although the main ImJoy app can go offline, and most of the plugins support offline, there still plugins require remote access to files, in that case, you won't be able to use those plugins without internet.

## Use the engine remotely.
You can use the Plugin Engine remotely on another computer. Due to security restrictions enforced by the browser, you won't be able to connect your remote plugin engine with https://imjoy.io , however, you can do it with the offline version of ImJoy. Just follow the instructions in **Go Offline**, and from the offline version, click the settings button, and you will be able set a remote url for the remote access.

## FAQs
 * Can I use my existing python?

  It depends whether it's a conda-compatible distribution or not, try to type `conda -V` command, if you see a version number(e.g:`conda 4.3.30`), it means you can skip the Anaconda/Miniconda installation, and install ImJoy directly with your existing python.
 * Can I use ImJoy with Python 2.7 or other version lower than Python 3.6?

  Yes, you can if you have the conda environment. You will be able to install and run ImJoy with Python version lower thant 3.6 (e.g.: Anaconda/Miniconda Python2.7). However, in that case, it will bootstrapping itself by creating a Python 3 environment (named `imjoy`) in order to run the actual plugin engine code. Therefore, Anaconda/Miniconda (Python3.6+ version) is still recommended if you have the choice.
 * What's the difference with [Anaconda](https://www.anaconda.com/download/) and [Miniconda](https://conda.io/miniconda.html)?

 Miniconda is just a reduced version of Anaconda. Since ImJoy only relies on `conda` which included by both, you can choose either of them. If you like minimal installation, choose Miniconda. If you want all those packages which will be used for scientific computing(such as numpy, scipy, scikit-image etc.), choose Anaconda.
 * Why I can't connect to my plugin engine run on a remote computer?

 First, you needs to make sure the other computer with plugin engine can be accessed from your current network and not blocked by a firewall for example.

 Second, currently you can't use ImJoy.io loaded with `https` with the Plugin Engine, because modern browsers do not allow you to make a insecured connection within a SSL secured website. So, you will have to switch to the offline version.

## Developing Python Plugins for ImJoy

See here for details: https://github.com/oeway/ImJoy
