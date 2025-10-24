#!/usr/bin/python3

import socket
import struct
import logging
import sys
import time

# ==============================================================================
# UDP Listener for EdgeCV4Safety
# ==============================================================================
# RUOLO:
# This script is executed as a subprocess by the main controllers
# (e.g. SpeedControllerUDP.py). Its sole purpose is to listen on a UDP port,
# receive packets containing the distance, and forward this data to the
# parent process via its standard output (stdout).
#
# COMMUNICATION:
# - INPUT: UDP packets on the specified port.
# - OUTPUT (data): Formatted strings to stdout (e.g. "DISTANCE:2.75\n").
# - OUTPUT (log): Status and error messages to stderr.
# ==============================================================================


# --- UDP Configuration ---
# These values must correspond to TARGET_NODE_IP and TARGET_NODE_PORT
# in the Computer Vision node.
LISTEN_IP = '192.168.37.50' # must correspond to TARGET_NODE_IP in the Computer Vision node or could be 0.0.0.0 to listen on all interfaces
LISTEN_PORT = 13750 # must correspond to TARGET_NODE_PORT of the sender

# ---logging Configuration ---
# DEBUG level for maximum verbosity.
# The stream is directed to stderr so that the parent process can
# capture it separately from the data sent to stdout.

def main():
    sock = None
    logging.info(f"Starting UDP listener for {LISTEN_IP}:{LISTEN_PORT}")

    try:
        # 1. Create the UDP socket
        logging.info("Creating UDP socket...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        logging.info("UDP socket created successfully.")

        # 2. Set socket options (crucial for robustness)
        # SO_REUSEADDR allows the socket to restart quickly without waiting
        # for the operating system to release the port (TIME_WAIT state).
        logging.info("Setting SO_REUSEADDR option...")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        logging.info("SO_REUSEADDR option set.")

        # Increase the OS-level receive buffer for the socket.
        # Reduces the likelihood of packet loss during bursts of data.
        logging.info("Setting receive buffer (SO_RCVBUF) to 1MB...")
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024) # 1 MB
        logging.info("Receive buffer set.")

        # 3. Binding of socket to address and port
        logging.info(f"Attempting to bind socket to {LISTEN_IP}:{LISTEN_PORT}...")
        try:
            sock.bind((LISTEN_IP, LISTEN_PORT))
            logging.info(f"UDP listener listening on {LISTEN_IP}:{LISTEN_PORT}. Waiting for packets...")
        except socket.error as e:
            # Critical error. If bind fails, the program cannot function.
            logging.info(f"BIND ERROR: Unable to bind to {LISTEN_IP}:{LISTEN_PORT}: {e}")
            sys.exit(1) # Exit with an error code that the parent can detect

        # 4. Main reception loop
        while True:  # The loop breaks when the parent process terminates it.
            try:
                # Attempt to receive data. The timeout is handled implicitly
                # by the fact that recvfrom is blocking. The process waits here.
                data, addr = sock.recvfrom(1024)  # Maximum buffer size

                # Check that the packet is the correct size for a float
                if len(data) == struct.calcsize('<f'):
                    # Decompress the bytes into a float value (little-endian)
                    received_distance = struct.unpack('<f', data)[0]

                    # 5. Send the data to the parent process via stdout
                    # The format "DISTANCE:value\n" is the communication "contract".
                    sys.stdout.write(f"DISTANCE:{received_distance}\n")
                    sys.stdout.flush()  # Essential to ensure immediate sending!

                    logging.info(f"Received and forwarded distance: {received_distance:.2f} m from {addr}")
                else:
                    logging.info(f"Received malformed packet from {addr}, size: {len(data)} bytes.")

            except Exception as e:
                # Handle other unexpected errors during reception.
                logging.error(f"Unexpected error during UDP reception: {e}", exc_info=True)
                time.sleep(0.1) # Brief pause to avoid flooding the logs in case of continuous error

    except socket.error as e:
        logging.info(f"CRITICAL ERROR: Unable to create or configure socket on {LISTEN_IP}:{LISTEN_PORT}: {e}", exc_info=True)
    except Exception as e:
        logging.info(f"Unrecoverable error in UDP receiver: {e}", exc_info=True)
    finally:
        # 6. Resource cleanup
        if sock:
            sock.close()
            logging.info("UDP socket closed.")
        logging.info("UDP receiver process terminated.")

if __name__ == "__main__":
    main()