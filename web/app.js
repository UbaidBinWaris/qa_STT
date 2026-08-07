document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const recordBtn = document.getElementById('recordBtn');
    const recordBtnText = document.getElementById('recordBtnText');
    const recordingTimer = document.getElementById('recordingTimer');
    const waveformCanvas = document.getElementById('waveformCanvas');
    const dropzone = document.getElementById('dropzone');
    const fileInput = document.getElementById('fileInput');
    const transcriptViewport = document.getElementById('transcriptViewport');
    const placeholderText = document.getElementById('placeholderText');
    const transcriptOutput = document.getElementById('transcriptOutput');
    const copyBtn = document.getElementById('copyBtn');
    const clearBtn = document.getElementById('clearBtn');
    const wordCount = document.getElementById('wordCount');
    const audioDuration = document.getElementById('audioDuration');

    // Audio & Canvas Contexts
    let isRecording = false;
    let mediaRecorder = null;
    let audioContext = null;
    let analyser = null;
    let websocket = null;
    let recordingStartTime = 0;
    let timerInterval = null;
    let canvasCtx = waveformCanvas.getContext('2d');

    // Initialize Waveform Canvas Visualizer
    function drawWaveform() {
        requestAnimationFrame(drawWaveform);
        const width = waveformCanvas.width;
        const height = waveformCanvas.height;
        canvasCtx.clearRect(0, 0, width, height);

        if (!analyser || !isRecording) {
            // Idle static wave line
            canvasCtx.beginPath();
            canvasCtx.moveTo(0, height / 2);
            canvasCtx.lineTo(width, height / 2);
            canvasCtx.strokeStyle = 'rgba(255, 255, 255, 0.1)';
            canvasCtx.lineWidth = 2;
            canvasCtx.stroke();
            return;
        }

        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);
        analyser.getByteTimeDomainData(dataArray);

        canvasCtx.lineWidth = 2;
        canvasCtx.strokeStyle = '#76b900';
        canvasCtx.beginPath();

        const sliceWidth = width * 1.0 / bufferLength;
        let x = 0;

        for (let i = 0; i < bufferLength; i++) {
            const v = dataArray[i] / 128.0;
            const y = v * height / 2;

            if (i === 0) {
                canvasCtx.moveTo(x, y);
            } else {
                canvasCtx.lineTo(x, y);
            }
            x += sliceWidth;
        }

        canvasCtx.lineTo(width, height / 2);
        canvasCtx.stroke();
    }
    drawWaveform();

    // Live Streaming Audio Recording
    recordBtn.addEventListener('click', async () => {
        if (!isRecording) {
            startRecording();
        } else {
            stopRecording();
        }
    });

    async function startRecording() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            
            // Connect WebSocket to Python STT backend server
            const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsHost = window.location.hostname || 'localhost';
            websocket = new WebSocket(`${wsProtocol}//${wsHost}:8000/ws/transcribe`);

            websocket.onmessage = (event) => {
                const data = JSON.parse(event.data);
                if (data.text) {
                    appendTranscription(data.text);
                }
            };

            audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
            const source = audioContext.createMediaStreamSource(stream);
            analyser = audioContext.createAnalyser();
            analyser.fftSize = 2048;
            source.connect(analyser);

            // ScriptProcessor for PCM audio chunk streaming
            const processor = audioContext.createScriptProcessor(4096, 1, 1);
            source.connect(processor);
            processor.connect(audioContext.destination);

            processor.onaudioprocess = (e) => {
                if (!isRecording || !websocket || websocket.readyState !== WebSocket.OPEN) return;
                const inputData = e.inputBuffer.getChannelData(0);
                // Convert float32 [-1, 1] to Int16 PCM bytes
                const pcm16 = new Int16Array(inputData.length);
                for (let i = 0; i < inputData.length; i++) {
                    const s = Math.max(-1, Math.min(1, inputData[i]));
                    pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
                }
                websocket.send(pcm16.buffer);
            };

            isRecording = true;
            recordBtn.classList.add('recording');
            recordBtnText.textContent = 'Stop Live Recording';
            recordingTimer.classList.remove('hidden');
            
            recordingStartTime = Date.now();
            timerInterval = setInterval(updateTimer, 1000);

            placeholderText.style.display = 'none';

        } catch (err) {
            alert('Microphone access error: ' + err.message);
        }
    }

    function stopRecording() {
        isRecording = false;
        recordBtn.classList.remove('recording');
        recordBtnText.textContent = 'Start Live Microphone';
        clearInterval(timerInterval);
        
        if (websocket) {
            websocket.close();
        }
        if (audioContext) {
            audioContext.close();
        }
    }

    function updateTimer() {
        const elapsedSeconds = Math.floor((Date.now() - recordingStartTime) / 1000);
        const mins = String(Math.floor(elapsedSeconds / 60)).padStart(2, '0');
        const secs = String(elapsedSeconds % 60).padStart(2, '0');
        recordingTimer.textContent = `${mins}:${secs}`;
        audioDuration.textContent = `${elapsedSeconds}.0s`;
    }

    // Audio File Drag and Drop Handling
    dropzone.addEventListener('click', () => fileInput.click());
    
    dropzone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropzone.classList.add('dragover');
    });

    dropzone.addEventListener('dragleave', () => {
        dropzone.classList.remove('dragover');
    });

    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('dragover');
        if (e.dataTransfer.files.length > 0) {
            processAudioFile(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            processAudioFile(e.target.files[0]);
        }
    });

    async function processAudioFile(file) {
        placeholderText.style.display = 'none';
        transcriptOutput.textContent = `[Processing file: ${file.name}...]`;

        const formData = new FormData();
        formData.append('file', file);

        try {
            const apiHost = window.location.hostname || 'localhost';
            const res = await fetch(`http://${apiHost}:8000/api/transcribe`, {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            if (data.transcription) {
                transcriptOutput.textContent = '';
                appendTranscription(data.transcription);
                audioDuration.textContent = `${data.duration_seconds}s`;
            }
        } catch (err) {
            transcriptOutput.textContent = `Error processing audio file: ${err.message}`;
        }
    }

    function appendTranscription(text) {
        placeholderText.style.display = 'none';
        transcriptOutput.textContent += (transcriptOutput.textContent ? ' ' : '') + text;
        
        // Update stats
        const words = transcriptOutput.textContent.trim().split(/\s+/).filter(w => w.length > 0);
        wordCount.textContent = words.length;

        // Auto-scroll to bottom of viewport
        transcriptViewport.scrollTop = transcriptViewport.scrollHeight;
    }

    // Copy to clipboard
    copyBtn.addEventListener('click', () => {
        const text = transcriptOutput.textContent;
        if (text) {
            navigator.clipboard.writeText(text);
            const originalText = copyBtn.innerHTML;
            copyBtn.innerHTML = 'Copied!';
            setTimeout(() => copyBtn.innerHTML = originalText, 2000);
        }
    });

    // Clear display
    clearBtn.addEventListener('click', () => {
        transcriptOutput.textContent = '';
        placeholderText.style.display = 'flex';
        wordCount.textContent = '0';
        audioDuration.textContent = '0.0s';
    });
});
