# Live Audio Transcription

## Overview
Live audio transcription is a real-time process of converting spoken words into written text using AI-driven speech recognition models. This is a desktop application. It converts audio to text and the text is then saved as notes in serviceNow and it also formats the notes using openai api by giving system prompt and user prompt

## Features
- **Real-Time Transcription**: Converts speech to text instantly.
- **AI-Powered Accuracy**: Uses advanced speech recognition models for precise results.
- **Multi-Language Support**: Supports transcription in multiple languages.

## Technology Stack
- **Programming Language**: Python
- **Speech Recognition Model**: AWS Transcribe
- **Frameworks & Libraries**: Tkinter

## Installation
### Prerequisites
- Python

### Steps
1. Clone the repository:
   ```bash
   git clone https://github.com/your-repo/live-audio-transcription.git
   cd live-audio-transcription
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration
Modify the `config.json` file to:
- use openai API to format the text
- Set up cloud API keys.
-use service now cred to update notes



## Contact
For issues and feature requests, reach out via [GitHub Issues](https://github.com/your-repo/live-audio-transcription/issues).
