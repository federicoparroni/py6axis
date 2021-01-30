# py6axis
This is a Python 3 wrapper to simplify handling input from the PlayStation 3 DualShock (Sixaxis) via Bluetooth. Credits to [ApproxEng/Triangula](https://github.com/ApproxEng/triangula).


## Install

```bash
pip install py6axis
```


## Pairing the controller (Linux)
The following steps are meant if you are on a Debian-based distro with a bluetooth connection already setup:

1. Install libusb-dev (used to interact with USB devices):
    ```bash
    sudo apt-get install libusb-dev
    ```

2. Download and install sixpair:
    ```bash
    mkdir ~/sixpair
    cd ~/sixpair
    wget http://www.pabr.org/sixlinux/sixpair.c
    gcc -o sixpair sixpair.c -lusb
    ```

3. With Sixpair now compiled, plug the PS3 controller using the USB mini cable and run:
    ```bash
    sudo ~/sixpair/sixpair
    ```

4. If the sixpair has successfully configured the PS3 Controller, then you should get the following response (with your bluetooth device MAC address):
    ```
    Current Bluetooth master: a1:b2:c3:d4:e5:d6
    Setting master bd_addr to a1:b2:c3:d4:e5:d6
    ```

5. Unplug the PS3 controller and open the bluetooth manager:
    ```bash
    sudo bluetoothctl
    ```

6. Type:
    ```
    agent on
    default-agent
    ```

7. Start the scanning and then press the controller PS button:
    ```
    scan on
    ```

8. You should see something like:
    ```
    [NEW] Device B8:27:EB:AA:BB:CC B8-27-EB-AA-BB-CC
    [CHG] Device B8:27:EB:AA:BB:CC Connected: no
    [DEL] Device B8:27:EB:AA:BB:CC B8-27-EB-AA-BB-CC
    ```

9. The procedure should ask if you would like to trust the controller. If so, say yes. Otherwise, do it manually:
    ```
    trust <CONTROLLER_MAC_ADDRESS>
    ```

10. If all went ok, you should see:
    ```
    [CHG] Device B8:27:EB:AA:BB:CC Trusted: yes
    Changing B8:27:EB:AA:BB:CC trust succeeded
    ```

11. Quit by typing `quit` or with the shortcut `CTRL + D`. From now on, if you press the PS button, the controller will automatically connect to your bluetooth device. Press and hold the PS button for 10 seconds to disconnect and turn the controller off. I don't know why, but sometimes the led 1 is on while connected, sometimes not.