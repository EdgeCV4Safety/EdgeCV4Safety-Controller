#!/usr/bin/python3

import socket
import struct
import logging
import threading
import sys
import select

# --- Parametri di configurazione del Ricevitore ---
LISTEN_IP = '192.168.37.50'  # Must correspond to TARGET_NODE_IP in the Computer Vision node
LISTEN_PORT = 13750          # Must correspond to TARGET_NODE_PORT of the sender

# --- Setup logging ---
logging.basicConfig(
    format='%(asctime)s.%(msecs)03d - %(message)s',
    datefmt='%H:%M:%S', # To add date: %Y-%m-%d
    level=logging.INFO
)

def run_udp_receiver(stop_event: threading.Event):
    """
    Start UDP server to listen for incoming distance data. Just debug/example purposes.
    """
    sock = None
    try:
        # Create a UDP socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Bind the socket to the IP address and port
        sock.bind((LISTEN_IP, LISTEN_PORT))
        logging.info(f"UDP receiver listening on {LISTEN_IP}:{LISTEN_PORT}")

        sock.settimeout(1.0) # Set a timeout for recvfrom to check the stop event

        while not stop_event.is_set():
            try:
                # Receive data
                data, addr = sock.recvfrom(1024)  # Buffer size 1024 bytes

                # Decompress the float value (assuming a single float, '<f' for little-endian float)
                if len(data) == struct.calcsize('<f'):
                    distance = struct.unpack('<f', data)[0]
                    logging.info(f"Distance received: {distance:.2f} m from {addr}")

                    
                else:
                    logging.warning(f"Malformed packet received from {addr}. Size: {len(data)} bytes. Expected {struct.calcsize('<f')} bytes.")

            except socket.timeout:
                # Timeout, continue the loop to check the stop event
                continue
            except Exception as e:
                logging.error(f"Error receiving UDP data: {e}")
                break
            
    except socket.error as e:
        logging.error(f"Unable to create or bind UDP socket: {e}")
    finally:
        if sock:
            sock.close()
            logging.info("UDP socket closed.")
        logging.info("UDP receiver thread terminated.")

def main():
    stop_event = threading.Event()
    
    receiver_thread = threading.Thread(target=run_udp_receiver, args=(stop_event,))
    receiver_thread.daemon = True # The thread will close with the main program
    receiver_thread.start()

    logging.info("Press 'q' and Enter to exit the receiver.")

    try:
        # Keep the main thread alive and listen for 'q' from the console
        while not stop_event.is_set():
            # Use select to check for input without blocking
            if sys.stdin in select.select([sys.stdin], [], [], 0.1)[0]: # Timeout 0.1s
                line = sys.stdin.readline().strip()
                if line == 'q':
                    logging.info("Pressed 'q'. Signaling shutdown.")
                    stop_event.set()
                    break
            
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt detected (Ctrl+C). Shutting down...")
        stop_event.set() # Signal the thread to stop
    finally:
        logging.info("Waiting for the receiver thread to terminate...")
        receiver_thread.join(timeout=5) # Wait for the thread to terminate gracefully
        if receiver_thread.is_alive():
            logging.warning("The UDP receiver thread did not terminate gracefully.")
        logging.info("UDP receiver application terminated.")

if __name__ == "__main__":
    main()