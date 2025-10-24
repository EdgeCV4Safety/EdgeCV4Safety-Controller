#!/usr/bin/python3
import logging
import threading
import sys
import time
import select
from queue import Queue, Empty
import subprocess
import os

# --- Settings for RTDE ---
sys.path.append("./rtde")
try:
    import rtde.rtde as rtde
    import rtde.rtde_config as rtde_config
except ImportError:
    print("Error: RTDE not found. Please ensure the path is correct and the library is installed.")
    sys.exit(1)

# --- Global configuration ---
ROBOT_HOST = "10.4.1.87" # Must correspond to the robot IP
ROBOT_PORT = 30004 # Must correspond to the robot RTDE port (default 30004)
CONFIG_XML = './recipe.xml' # Make sure this is the correct path to recipe.xml
RTDE_FREQUENCY = 100 # Hz

# --- Speed thresholds ---
VELOCITY_THRESHOLD_0_5M      = 0.0
VELOCITY_THRESHOLD_1M        = 0.3
VELOCITY_2M_THRESHOLD        = 0.7
VELOCITY_3M_THRESHOLD        = 1.0
VELOCITY_OVER_3M_THRESHOLD   = 1.0

# --- Logging configuration ---
logging.basicConfig(
    format='%(asctime)s.%(msecs)03d - %(message)s',
    datefmt='%H:%M:%S', # To add date: %Y-%m-%d
    level=logging.INFO
)

# --- Distance queue ---
distance_queue = Queue()

def calculate_speed_fraction(distance: float) -> float:
    """
    Calculate the speed fraction based on the distance.
    """
    if distance < 0:
        return VELOCITY_OVER_3M_THRESHOLD
    elif distance < 1:
        return VELOCITY_THRESHOLD_0_5M
    elif 1 <= distance < 2:
        return VELOCITY_THRESHOLD_1M
    elif 2 <= distance < 3:
        return VELOCITY_2M_THRESHOLD
    elif 3 <= distance < 4:
        return VELOCITY_3M_THRESHOLD
    else:
        return VELOCITY_OVER_3M_THRESHOLD

def run_rtde_controller(stop_event: threading.Event):
    """
    Thread for controlling the robot via RTDE.
    """
    con = None
    input_data = None
    current_distance = -1.0
    previous_speed_fraction = -1.0 # Variable to store the last sent speed fraction

    # The outer loop handles RTDE reconnection attempts
    while not stop_event.is_set():
        try:
            logging.info("[RTDE_TX] Attempting to connect to UR robot...")
            con = rtde.RTDE(ROBOT_HOST, ROBOT_PORT)

            # --- RTDE CONNECTION LOOP ---
            retries = 0
            MAX_RETRIES = 5 # Increased retries to avoid immediate failure
            while not con.is_connected() and not stop_event.is_set():
                if retries >= MAX_RETRIES:
                    logging.info(f"[RTDE_TX] Unable to connect after {MAX_RETRIES} attempts. Retrying full sequence in 10 seconds.")
                    raise ConnectionRefusedError("RTDE connection failed repeatedly")

                try:
                    con.connect()
                    if con.is_connected():
                        logging.info("[RTDE_TX] Connected to the robot.")
                        break # Exit the connection loop if successful
                except Exception as e:
                    logging.info(f"[RTDE_TX] Connection error: {e}. Retrying in 2 seconds...")
                time.sleep(2) # Pause between attempts
                retries += 1

            if not con.is_connected():
                raise ConnectionRefusedError("RTDE connection not established")

            conf = rtde_config.ConfigFile(CONFIG_XML)
            input_names, input_types = conf.get_recipe('in')
            output_names, output_types = conf.get_recipe('out')

            # --- RTDE SETUP ORDER IS CRUCIAL ---
            # 1. Setup Input (data to change)
            logging.info("[RTDE_TX] Attempting send_input_setup...")
            input_data = con.send_input_setup(input_names, input_types)
            if not input_data:
                logging.info("[RTDE_TX] Error configuring RTDE input. Terminating RTDE thread.")
                raise Exception("Error configuring RTDE input")
            logging.info("[RTDE_TX] send_input_setup completed.")

            # Initialize the mask and slider fraction in the data packet
            # Make sure these attributes exist on the input_data object
            if hasattr(input_data, 'speed_slider_mask'):
                input_data.speed_slider_mask = 1
            else:
                logging.info("[RTDE_TX] 'speed_slider_mask' not found in input recipe. Unable to control speed slider.")

            if hasattr(input_data, 'speed_slider_fraction'):
                input_data.speed_slider_fraction = VELOCITY_OVER_3M_THRESHOLD # Initialize to 100%
            else:
                logging.info("[RTDE_TX] 'speed_slider_fraction' not found in input recipe.")

            # 2. Setup Output
            logging.info("[RTDE_TX] Attempting send_output_setup...")
            if not con.send_output_setup(output_names, output_types, RTDE_FREQUENCY):
                logging.info("[RTDE_TX] Error configuring RTDE output. Terminating RTDE thread.")
                raise Exception("Error configuring RTDE output")
            logging.info("[RTDE_TX] send_output_setup completed.")

            # 3. Starting RTDE Synchronization
            logging.info("[RTDE_TX] Attempting send_start...")
            if not con.send_start():
                logging.info("[RTDE_TX] RTDE synchronization failed (send_start). Terminating RTDE thread.")
                raise Exception("Error send_start RTDE")
            logging.info("[RTDE_TX] RTDE started and synchronized.")

            # --- MAIN RTDE COMMUNICATION LOOP ---
            while not stop_event.is_set() and con.is_connected():
                state = con.receive() # Receive a state packet from the robot
                if state:
                    # Read the last available distance from the queue, emptying the previous ones
                    try:
                        while not distance_queue.empty(): # Empty the queue to get only the latest value
                            current_distance = distance_queue.get_nowait()
                        # Here, current_distance will be the last value put in the queue, or its previous value if the queue was empty
                    except Empty:
                        pass # The queue was empty, use the last known current_distance

                    # Calculate the new speed fraction based on the distance
                    new_speed_fraction = calculate_speed_fraction(current_distance)

                    # Send the new speed fraction only if it has changed from the last sent one
                    if input_data and hasattr(input_data, 'speed_slider_fraction') and \
                       new_speed_fraction != previous_speed_fraction:
                        
                        input_data.speed_slider_fraction = new_speed_fraction
                        con.send(input_data) # <--- SEND THE SPEED SLIDER
                        previous_speed_fraction = new_speed_fraction # Update the value for the next comparison
                        logging.info(f"[RTDE_TX] Distance: {current_distance:.2f} m -> Set Speed: {new_speed_fraction*100:.0f}%")

                    # Log the robot data (e.g., TCP speed) for debugging
                    log_robot_data = f"[RTDE_RX] "
                    if hasattr(state, 'actual_TCP_speed'):
                        log_robot_data += f"Actual TCP Speed: {state.actual_TCP_speed} | "
                    if hasattr(state, 'target_TCP_speed'):
                        log_robot_data += f"Target TCP Speed: {state.target_TCP_speed}"

                    #logging.info(log_robot_data) # Enable for continuous logging of robot data

                elif state is None:
                    # This happens if there is no data available in the RTDE buffer for the current frequency.
                    # It is not necessarily a connection error, but it may indicate that the connection is slow
                    # or that the robot is not sending data at the expected frequency.
                    logging.info("[RTDE_TX] No RTDE packet received. Check connection or frequency.")

                # The pause between each cycle of the RTDE loop is based on the frequency
                time.sleep(1 / RTDE_FREQUENCY)

        except ConnectionRefusedError as e:
            logging.info(f"[RTDE_TX] Connection to robot refused or not established: {e}. Retrying in 10s...")
            time.sleep(10) # Longer pause before retrying the full connection
        except Exception as e:
            logging.info(f"[RTDE_TX] .info error in RTDE thread: {e}. Retrying full sequence in 5 seconds.", exc_info=True)
            time.sleep(5) # Pause before retrying the connection after a generic error
        finally:
            # Ensure a clean disconnection in any case
            if con and con.is_connected():
                try:
                    logging.info("[RTDE_TX] Sending send_pause before disconnection.")
                    con.send_pause() # Important to release controls
                    logging.info("[RTDE_TX] RTDE disconnection in progress.")
                    con.disconnect()
                    logging.info("[RTDE_TX] Connessione RTDE disconnessa con successo.")
                except Exception as e:
                    logging.info(f"[RTDE_TX] Errore durante la disconnessione RTDE: {e}")
            con = None
            # Significant pause to give time to robot to clean up state
            logging.info("[RTDE_TX] Significant pause of 30 seconds before a new RTDE connection attempt.")
            time.sleep(30) # Increased pause here to avoid aggressive reconnections

    logging.info("[RTDE_TX] Thread RTDE ended.")

# --- main ---
def main():
    stop_event = threading.Event()
    
    udp_process = None
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        udp_script_path = os.path.join(script_dir, "udp_listener.py")

        logging.info(f"Start UDP subprocess: {sys.executable} {udp_script_path}")
        # Important modification: bufsize=1 (line buffered) to ensure line-by-line output.
        # bufsize=0 is "unbuffered" and can cause performance issues or race conditions
        # when reading from a pipe in real-time with readline(). bufsize=1 is better for text=True.
        udp_process = subprocess.Popen(
            [sys.executable, udp_script_path], 
            stdout=subprocess.PIPE,   
            stderr=subprocess.PIPE,   
            text=True,                
            bufsize=1, # Changed from 0 to 1 for line buffering               
            preexec_fn=os.setsid      
        )
        logging.info(f"UDP subprocess started with PID: {udp_process.pid}")
    except FileNotFoundError:
        logging.info(f"Error: File '{udp_script_path}' not found. Please ensure it exists and is executable.")
        sys.exit(1)
    except Exception as e:
        logging.info(f"Error starting UDP subprocess: {e}", exc_info=True)
        sys.exit(1)

    rtde_thread = threading.Thread(target=run_rtde_controller, args=(stop_event,), daemon=True)
    rtde_thread.start()

    logging.info("Press 'q' and Enter to exit.")

    poller = select.poll()
    poller.register(udp_process.stdout, select.POLLIN)
    poller.register(udp_process.stderr, select.POLLIN)
    poller.register(sys.stdin, select.POLLIN)

    try:
        while not stop_event.is_set():
            ready_fds = poller.poll(100) # Timeout increased to 100ms to reduce aggressive polling

            for fd, event in ready_fds:
                if fd == udp_process.stdout.fileno():
                    line = udp_process.stdout.readline()
                    if line:
                        if line.startswith("DISTANCE:"):
                            try:
                                received_distance = float(line.strip().split(':')[1])
                                # The use of mutex is not necessary for queue.Queue, it is already thread-safe.
                                # Remove the mutex lock for simplicity and to avoid potential deadlocks.
                                # Remove the while True loop to drain the queue here.
                                # The queue should be drained in the consumer thread (RTDE).
                                # Here we just need to add the latest value.
                                distance_queue.put(received_distance)
                                logging.info(f"[MAIN_PROC] Distance (PUT): {received_distance:.2f} m")
                            except ValueError:
                                logging.info(f"[MAIN_PROC] Malformed UDP line: {line.strip()}")
                        else:
                            logging.info(f"[MAIN_PROC] Unknown UDP output: {line.strip()}")
                elif fd == udp_process.stderr.fileno():
                    err_line = udp_process.stderr.readline()
                    if err_line:
                        logging.info(f"[MAIN_PROC_UDP_ERR] {err_line.strip()}")
                elif fd == sys.stdin.fileno():
                    line = sys.stdin.readline().strip()
                    if line == 'q':
                        logging.info("User requested interruption. Closing...")
                        stop_event.set()
                    else:
                        logging.info(f"Ignored input: '{line}'. Press 'q' to exit.")

            if udp_process.poll() is not None:
                logging.info(f"UDP subprocess terminated unexpectedly with code: {udp_process.returncode}")
                # Read all remaining stderr output in case of crash for more info
                stderr_output = udp_process.stderr.read()
                if stderr_output:
                    logging.info(f"Remaining stderr output from UDP subprocess:\n{stderr_output}")
                stop_event.set()
                break

    except KeyboardInterrupt:
        logging.info("Keyboard interrupt detected (Ctrl+C). Shutting down...")
        stop_event.set()
    finally:
        logging.info("Waiting for threads and processes to terminate...")
        time.sleep(0.1) # Brief pause to avoid minor race conditions

        # Handle UDP subprocess termination
        if udp_process and udp_process.poll() is None:
            logging.info("Sending termination signal to UDP subprocess...")
            udp_process.terminate() # Send SIGTERM
            try:
                udp_process.wait(timeout=2) # Wait for graceful termination
            except subprocess.TimeoutExpired:
                logging.info("UDP subprocess did not terminate gracefully, killing it.")
                udp_process.kill() # Send SIGKILL

        # Wait for RTDE thread termination
        rtde_thread.join(timeout=5)
        if rtde_thread.is_alive():
            logging.info("RTDE thread did not terminate gracefully within the timeout.")

        logging.info("All components terminated. Exiting program.")

if __name__ == "__main__":
    main()