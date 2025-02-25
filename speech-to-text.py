import asyncio
import sounddevice as sd
import customtkinter as ctk
from amazon_transcribe.client import TranscribeStreamingClient
from amazon_transcribe.handlers import TranscriptResultStreamHandler
from amazon_transcribe.model import TranscriptEvent
import threading
import requests
from requests.auth import HTTPBasicAuth
import json
import os
import logging


class TranscriptionHandler(TranscriptResultStreamHandler):
    def __init__(self, output_stream, result_text, transcription_service):
        super().__init__(output_stream)
        self.previous_transcript = ""
        self.result_text = result_text
        self.transcription_service = (
            transcription_service  # Store the transcription service instance
        )

    async def handle_transcript_event(self, transcript_event: TranscriptEvent):
        if self.transcription_service.paused:  # Check if paused
            return  # Skip processing if paused

        results = transcript_event.transcript.results
        for result in results:
            if not result.is_partial:
                for alt in result.alternatives:
                    if alt.transcript != self.previous_transcript:
                        self.previous_transcript = alt.transcript
                        logging.info(f"Transcript: {alt.transcript}")
                        self.result_text.insert("end", alt.transcript + "\n")
                        self.transcription_service.transcript_file.write(
                            alt.transcript + "\n"
                        )
                        self.transcription_service.transcript_file.flush()


class TranscriptionService:
    def __init__(self, loop):
        self.loop = loop
        self.recording = False
        self.paused = False
        self.transcription_task = None
        self.transcription_future = None
        self.transcript_file = None

    async def mic_stream(self):
        input_queue = asyncio.Queue()

        def callback(indata, frame_count, time_info, status):
            if self.recording:  # Only put data in queue if not paused
                self.loop.call_soon_threadsafe(
                    input_queue.put_nowait, (bytes(indata), status)
                )

        stream = sd.InputStream(
            channels=1,
            samplerate=16000,
            callback=callback,
            blocksize=1024 * 2,
            dtype="int16",
        )

        with stream:
            while self.recording:
                if not self.paused:
                    indata, status = await input_queue.get()
                    yield indata, status
                else:
                    # Clear input queue during pause to avoid processing buffered data
                    while not input_queue.empty():
                        input_queue.get_nowait()
                    # Send silent audio data to keep the connection alive
                    silent_audio = bytes([0] * 16000 * 2)
                    yield silent_audio, None
                    await asyncio.sleep(0.25)

    async def write_chunks(self, stream):
        mic_stream_generator = self.mic_stream()
        while self.recording:
            try:
                indata, status = await mic_stream_generator.__anext__()
                await stream.input_stream.send_audio_event(audio_chunk=indata)
            except StopAsyncIteration:
                break
            except Exception as e:
                logging.error(f"Error sending audio event: {e}")
                await asyncio.sleep(1)
        await stream.input_stream.end_stream()

    async def transcribe(self, language_code, result_text, file_name):
        logging.info(f"Starting transcription for {file_name} with language code {language_code}")
        client = TranscribeStreamingClient(region=config["aws"]["region"])
        stream = await client.start_stream_transcription(
            language_code=language_code,
            media_sample_rate_hz=16000,
            media_encoding="pcm",
        )
        self.transcript_file = open(file_name, "w")
        handler = TranscriptionHandler(
            stream.output_stream, result_text, self
        )  # Pass self (TranscriptionService)
        self.transcription_future = asyncio.ensure_future(handler.handle_events())
        self.transcription_task = asyncio.ensure_future(self.write_chunks(stream))
        try:
            await asyncio.gather(self.transcription_task, self.transcription_future)
        except asyncio.CancelledError:
            logging.info("Transcription task was cancelled.")
        finally:
            self.transcript_file.close()
            logging.info(f"Transcription finished for {file_name}")

    def start_transcription(
        self, language_code, customer_name, incident_ref, result_text
    ):
        self.recording = True
        file_name = f"{customer_name}_{language_code}_{incident_ref}.txt"
        result_text.insert("1.0", "You can speak now\n")
        logging.info(f"Starting transcription for incident {incident_ref}")
        threading.Thread(
            target=lambda: self.loop.run_until_complete(
                self.transcribe(language_code, result_text, file_name)
            )
        ).start()

    def pause_transcription(self):
        if self.recording:
            self.paused = not self.paused
            logging.info(f"Transcription paused: {self.paused}")

    def stop_transcription(self):
        self.recording = False
        self.paused = False
        if self.transcription_task:
            self.transcription_task.cancel()
        if self.transcription_future:
            self.transcription_future.cancel()
        if self.transcript_file:
            self.transcript_file.close()
        logging.info("Transcription stopped")


class IncidentService:
    def __init__(self):
        self.instance_url = config["service_now"]["instance_url"]
        self.auth = HTTPBasicAuth(
            config["service_now"]["username"], config["service_now"]["password"]
        )

    def attach_notes(self, file, incident_number, language_code):
        logging.info(f"Attaching notes for incident {incident_number} in {language_code}")
        with open(file, "r") as work_notes:
            work_notes_content = work_notes.read()

        url = config["azure_api"]["url"]
        payload = json.dumps(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": f"You are an AI assistant that helps DEVOPS Engineer to write ticket work notes, that will contains all the actions perform by the agents, format with bullet point. Language is in {language_code}",
                    },
                    {
                        "role": "user",
                        "content": f"Please find below the transcription of Engineer : {work_notes_content}",
                    },
                ],
                "max_tokens": 800,
                "temperature": 0.1,
                "frequency_penalty": 0,
                "presence_penalty": 0,
                "top_p": 0.95,
                "stop": None,
            }
        )
        headers = {
            "Content-Type": "application/json",
            "api-key": config["azure_api"]["api_key"],
        }

        response = requests.request("POST", url, headers=headers, data=payload)
        # Convertir la réponse en JSON
        response_json = response.json()
        print("response_json: ", response_json["choices"][0]["message"]["content"])

        work_notes_to_write = response_json["choices"][0]["message"]["content"]

        payload = json.dumps(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": f"You are an AI assistant that helps DEVOPS Engineer to write ticket close notes, that will contains Symptom, Diagnostic and Step to resolve, format with bullet point. Language is in {language_code}",
                    },
                    {
                        "role": "user",
                        "content": f"Please find below the transcription of Engineer : {work_notes_content}",
                    },
                ],
                "max_tokens": 800,
                "temperature": 0.1,
                "frequency_penalty": 0,
                "presence_penalty": 0,
                "top_p": 0.95,
                "stop": None,
            }
        )
        headers = {
            "Content-Type": "application/json",
            "api-key": config["azure_api"]["api_key"],
            "Cookie": "XSRF-TOKEN=32fec8ef-da34-4f59-a497-4e1e7373735c",
        }

        response = requests.request("POST", url, headers=headers, data=payload)
        # Convertir la réponse en JSON
        response_json = response.json()
        # print('response_json: ', response_json["choices"][0]["message"]["content"])

        close_notes_to_write = response_json["choices"][0]["message"]["content"]

        print(response.text)

        query_url = f"{self.instance_url}/api/now/table/incident?sysparm_query=number={incident_number}"
        response = requests.get(
            query_url, auth=self.auth, headers={"Content-Type": "application/json"}
        )
        print(response)
        if response.status_code == 200:
            results = response.json().get("result")
            print(results)
            if results:
                sys_id = results[0].get("sys_id")
                url = f"{self.instance_url}/api/now/table/incident/{sys_id}"
                payload = {
                    "work_notes": work_notes_to_write,
                    "close_notes": close_notes_to_write,
                    "close_code": "Solution provided",
                    "incident_state": "6",
                }
                json_payload = json.dumps(payload)
                update_response = requests.patch(
                    url,
                    auth=self.auth,
                    headers={"Content-Type": "application/json"},
                    data=json_payload,
                )

                if update_response.status_code == 200:
                    print("Work notes updated successfully!")
                    return "Work notes updated successfully!"
                else:
                    print(
                        f"Failed to update work notes. Status code: {update_response.status_code}"
                    )
                    print(update_response.text)
                    return "Failed to update work notes"
            else:
                print("Incident not found.")
                return "Incident not found."
        else:
            print(f"Failed to retrieve incident. Status code: {response.status_code}")
            print(response.text)
            return "Failed to retrieve incident"


class TranscriptionApp:
    def __init__(self, root, loop):
        self.root = root
        self.loop = loop
        self.transcription_service = TranscriptionService(loop)
        self.incident_service = IncidentService()

        self.setup_gui()

    def setup_gui(self):
        self.root.title("Audio Transcription")
        self.root.iconbitmap(".\\img\\demo.ico")   #CHOOSE YOUR ICON
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("green")

        top_frame = ctk.CTkFrame(self.root)
        top_frame.pack(pady=20)

        self.customer_label = ctk.CTkLabel(top_frame, text="Select Customer")
        self.customer_label.grid(row=0, column=0, padx=10, pady=5, sticky="w")
        self.customer_dropdown = ctk.CTkComboBox(
            top_frame, values=["Customer-1", "Customer-2"]
        )
        self.customer_dropdown.grid(row=0, column=1, padx=10, pady=5)

        self.incident_label = ctk.CTkLabel(top_frame, text="Incident Reference")
        self.incident_label.grid(row=1, column=0, padx=10, pady=5, sticky="w")
        self.incident_entry = ctk.CTkEntry(top_frame)
        self.incident_entry.grid(row=1, column=1, padx=10, pady=5)

        self.language_label = ctk.CTkLabel(top_frame, text="Language")
        self.language_label.grid(row=2, column=0, padx=10, pady=5, sticky="w")
        self.language_dropdown = ctk.CTkComboBox(top_frame, values=["en-US", "fr-FR"])
        self.language_dropdown.set("en-US")
        self.language_dropdown.grid(row=2, column=1, padx=10, pady=5)

        buttons_frame = ctk.CTkFrame(self.root)
        buttons_frame.pack(pady=20)

        self.start_button = ctk.CTkButton(
            buttons_frame, text="Start", command=self.start_transcription
        )
        self.start_button.grid(row=0, column=0, padx=20, pady=10)

        self.pause_button = ctk.CTkButton(
            buttons_frame,
            text="Pause",
            hover_color="sienna",
            fg_color="sienna2",
            command=self.toggle_pause,
        )
        self.pause_button.grid(row=0, column=1, padx=20, pady=10)

        stop_button = ctk.CTkButton(
            buttons_frame,
            text="Stop",
            hover_color="darkred",
            fg_color="firebrick",
            command=self.stop_transcription,
        )
        stop_button.grid(row=0, column=2, padx=20, pady=10)

        result_label = ctk.CTkLabel(buttons_frame, text="Transcription Result:")
        result_label.grid(row=1, column=0, columnspan=3, pady=10)

    def start_transcription(self):
        try:
            customer_name = self.customer_dropdown.get()
            incident_ref = self.incident_entry.get()
            language_code = self.language_dropdown.get()
            self.start_button.configure(state="disabled")
            self.result_text = ctk.CTkTextbox(self.root, width=500, height=200)
            self.result_text.pack(pady=20)
            logging.info(f"Starting transcription process for {customer_name} - Incident: {incident_ref}")
            self.transcription_service.start_transcription(
                language_code, customer_name, incident_ref, self.result_text
            )
            self.pause_button.configure(text="Pause")
        except Exception as e:
            logging.error(f"Error starting transcription: {e}")

    def toggle_pause(self):
        if self.transcription_service.recording:
            if self.transcription_service.paused:
                self.pause_button.configure(text="Pause")
            else:
                self.pause_button.configure(text="Resume")
            self.transcription_service.pause_transcription()

    def stop_transcription(self):
        self.overlay = ctk.CTkFrame(
            self.root,
            bg_color="#000000",
            fg_color="#000000",
            width=self.root.winfo_width(),
            height=self.root.winfo_height(),
        )
        self.overlay.place(x=0, y=0, relwidth=1.5, relheight=1)

        self.overlay.grid_rowconfigure(0, weight=1)
        self.overlay.grid_columnconfigure(0, weight=1)
        self.overlay.grid_rowconfigure(1, weight=1)
        self.overlay.grid_columnconfigure(1, weight=1)
        self.loading_label = ctk.CTkLabel(
            self.overlay,
            text="In Progress...",
            font=("Helvetica", 20),
            text_color="orange",
        )
        # self.loading_label.pack(pady=10)
        self.loading_label.grid(row=1, column=0, padx=10, pady=10)

        self.progress_bar = ctk.CTkProgressBar(self.overlay, mode="indeterminate")
        # self.progress_bar.pack(fill='x', padx=20, pady=5)
        self.progress_bar.grid(row=0, column=0, padx=50, pady=50)
        self.overlay.grid_propagate(False)
        self.progress_bar.start()

        threading.Thread(target=self._complete_transcription_process).start()

    def _complete_transcription_process(self):
        try:
            logging.info("Stopping transcription and updating notes")
            self.transcription_service.stop_transcription()

            language_code = self.language_dropdown.get()
            file_name = f"{self.customer_dropdown.get()}_{language_code}_{self.incident_entry.get()}.txt"
            return_msg = self.incident_service.attach_notes(
                file_name, self.incident_entry.get(), language_code
            )
        
        except Exception as e:
            logging.error(f"Error stopping transcription: {e}")

        finally:
            self.root.after(0, self._cleanup_widgets, return_msg)

    def _cleanup_widgets(self, message):
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.loading_label.pack_forget()
        self.loading_label1 = ctk.CTkLabel(
            self.overlay, text=message, font=("Helvetica", 20), text_color="light green"
        )
        self.loading_label1.grid(row=1, column=0, padx=10, pady=10)
        self.root.after(5000, self.root.destroy)


if __name__ == "__main__":
    log_dir = './logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    logging.basicConfig(
        filename=".\\logs\\audio_transcription.log",
        filemode="a",
        format="%(asctime)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )

    with open("config.json") as config_file:
        config = json.load(config_file)

    os.environ["AWS_ACCESS_KEY_ID"] = config["aws"]["access_key_id"]
    os.environ["AWS_SECRET_ACCESS_KEY"] = config["aws"]["secret_access_key"]
    os.environ["AWS_DEFAULT_REGION"] = config["aws"]["region"]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    root = ctk.CTk()
    root.geometry("600x600")
    app = TranscriptionApp(root, loop)
    root.mainloop()
