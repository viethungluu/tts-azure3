import os
from tempfile import gettempdir

import uuid
import logging

from google.cloud import storage
import azure.cognitiveservices.speech as speechsdk

import ffmpeg
# from pydub import AudioSegment
# from pydub.exceptions import PydubException


def _textToSsml(text: str, 
                lang_code: str, 
                voice_id: str, 
                pitch:float=0, 
                rate: float=0, 
                volume:float=0):
    """Convert text and voice params to Azure Cognitive Services compatible SSMl

    Args:
        text (str): text content
        lang_code (str): language code. i.e. en-US
        voice_id (str): voice name. i.e. pt-BR-FranciscaNeural
        pitch (float, optional): pitch. Defaults to 0.
        rate (float, optional): speaking rate. Defaults to 1.
        volume (float, optional): volume. Defaults to 0.

    Returns:
        str: generated SSMl
    """    
    pitch = "%+f" % (pitch)
    # volume = "%+f" % (volume)
    # return """<speak version="1.0" xmlns="https://www.w3.org/2001/10/synthesis" xml:lang="{0}"><voice name="{1}"><prosody pitch="{2}%" rate="{3}%" volume="{4}%">{5}</prosody></voice></speak>""".format(lang_code, voice_id, pitch, rate, volume, text)
    
    return """<speak version="1.0" xmlns="https://www.w3.org/2001/10/synthesis" xml:lang="en-US"><voice name="{1}"><prosody pitch="{2}st" rate="{3}%">{5}</prosody></voice></speak>""".format(lang_code, voice_id, pitch, rate, volume, text)

def _upload_blob(bucket_name: str, 
                 source_file_name: str, 
                 destination_blob_name: str,
                 make_public: bool=True):
    """Upload file to GCP Storage

    Args:
        bucket_name (str): GCP Storage bucket name
        source_file_name (str): local filepath
        destination_blob_name (str): bucket filepath
        make_public (bool, optional): make blob publicy access. Defaults to True.
    """    
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)

    blob.upload_from_filename(source_file_name, content_type="file/basic")
    if make_public:
        blob.make_public()

def _chunk_text(text: str,
                max_length: int=3000,
                min_length: int=250):
    """Split long text into smaller text segment.

    Args:
        text (str): text content
        max_length (int, optional): Maximum length of each text segment. Defaults to 3000.
        min_length (int, optional): Minimum length of a text segment. Defaults to 250.
    """                
    rest = text

    text_blocks = []
    while (len(rest) > max_length):
        begin = 0
        end = rest.find(".", max_length)

        if (end == -1):
            end = rest.find(" ", max_length)
            
        segment = rest[begin:end]
        rest = rest[end:]
        text_blocks.append(segment)
    
    if len(rest) > 0:
        if len(rest) < min_length and len(text_blocks) > 0:
            text_blocks[-1] = text_blocks[-1] + rest
        else:
            text_blocks.append(rest)

    return text_blocks

def synthesize_speech(request):
    basename = str(uuid.uuid4())

    tempdir = os.path.join(gettempdir(), basename)
    try: 
        os.mkdir(tempdir) 
    except OSError as e:
        logging.error(e)
        return None, 500

    speech_config = speechsdk.SpeechConfig(subscription=os.getenv('AzureSubcription'), region=os.getenv('AzureRegion'))
    speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3)

    try:
        request_json = request.get_json()
        if request.args and 'content' in request.args:
            pitch = request.args.get("pitch")
            rate = request.args.get("speakingRate")
            volume = request.args.get("volumeGainDb")
            voice_id = request.args.get("voiceId")
            lang_code = request.args.get("langCode")
            content = request.args.get("content")
            content_type = request.args.get("contentType")
        elif request_json and 'content' in request_json:
            pitch = request_json["pitch"]
            rate = request_json["speakingRate"]
            volume = request_json["volumeGainDb"]
            voice_id = request_json["voiceId"]
            lang_code = request_json["langCode"]
            content = request_json["content"]
            content_type = request_json["contentType"]
        else:
            return None, 500
        
    except ValueError as e:
        logging.error(e)
        return None, 500

    text_blocks = _chunk_text(content)
    for i, segment in enumerate(text_blocks):
        ssml = _textToSsml(segment, lang_code, voice_id, pitch, rate, volume)
        
        file_config = speechsdk.audio.AudioOutputConfig(filename=os.path.join(tempdir, "{0}.mp3".format(i)))
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=file_config)

        result = synthesizer.speak_ssml(ssml)
        if result.reason == speechsdk.ResultReason.Canceled:
            cancellation_details = result.cancellation_details
            if cancellation_details.reason == speechsdk.CancellationReason.Error:
                if cancellation_details.error_details:
                    logging.error(cancellation_details.error_details)
            return None, 500

    try:
        streams = []
        for i in range(len(text_blocks)):
            streams.append(ffmpeg.input(os.path.join(tempdir, "{0}.mp3".format(i))))
        ffmpeg.concat(*streams, v=0, a=1).output(os.path.join(tempdir, "audio.wav")).run()
    except ffmpeg.Error as e:
        logging.error(e)
    
    try:
        _upload_blob(os.getenv('CloudBucket'), os.path.join(tempdir, "audio.wav"), "{0}.wav".format(basename))
    except Exception as e:
        logging.error(e)
        return None, 500
    
    response = {
        "voiceUrl" : "https://storage.googleapis.com/{0}/{1}".format(os.getenv('CloudBucket'), "{0}.wav".format(basename))
    }
    return response, 200