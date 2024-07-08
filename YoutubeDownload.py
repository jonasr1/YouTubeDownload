import logging
import os
import sys
import time
from typing import Tuple, Optional

import requests
from moviepy.editor import VideoFileClip, AudioFileClip
from pytube import YouTube, StreamQuery
from pytube.exceptions import VideoUnavailable, RegexMatchError
from tqdm import tqdm

global audio_path, video_path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def get_video_details(url: str) -> Tuple[Optional[str], Optional[StreamQuery], Optional[YouTube]]:
    """
    Obtém detalhes de um vídeo do YouTube a partir da URL fornecida.

    Parâmetros:
    url (str): URL do vídeo do YouTube.

    Retorna:
    tuple: Título do vídeo (str), streams disponíveis (StreamQuery) e objeto YouTube, ou (None, None, None) em caso de erro.
    """
    try:
        yt = YouTube(url)
        title = yt.title
        streams = yt.streams
        return title, streams, yt
    except VideoUnavailable:
        logging.error("Vídeo indisponível. Verifique o link do vídeo.")
    except RegexMatchError:
        logging.error("URL inválida. Verifique o link do vídeo.")
    except Exception as e:
        logging.error(f"Ocorreu um erro ao obter o vídeo: {str(e)}")
    return None, None, None


def retrieve_available_streams(streams):
    available_streams = []
    for stream in streams:
        if (resolution := stream.resolution) and (file_size := stream.filesize):
            audio_size = get_audio_size(stream=stream, streams=streams)
            stream_info = f"{resolution} - {format_file_size(file_size)}{audio_size} - {stream.mime_type.split('/')[1]}{f' - {stream.abr}' if stream.abr else ''}"
            available_streams.append((stream_info, stream))
    return sort_streams(available_streams)


def sort_streams(streams):
    """
    Ordena streams pela resolução numérica, tratando casos onde a resolução não pode ser convertida.

    Parâmetros:
    streams: Lista de streams com informações e objetos de stream.

    Retorna:
    list: Lista de streams ordenada por resolução ou tamanho do arquivo.
    """
    try:
        streams.sort(key=lambda x: int(x[0].split('p')[0].strip()))
    except ValueError as e:
        logging.warning(f"Erro ao ordenar streams pela resolução, ordenando por tamanho do arquivo: {e}")
        streams.sort(key=lambda x: x[1])  # Ordena por tamanho do arquivo como fallback
    return streams


def get_audio_size(stream, streams):
    if not stream.includes_audio_track:
        audio_stream = streams.filter(only_audio=True).order_by('abr').desc().first()
        if audio_stream:
            return f" + {format_file_size(audio_stream.filesize)} (áudio separado)"
    return ''


def format_file_size(filesize):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if filesize < 1024:
            return f"{filesize:.2f} {unit}"
        filesize /= 1024


def retry_download(attempt, max_attempts, wait_time):
    """
    Parâmetros:
    attempt: Número atual da tentativa.
    max_attempts: Número máximo de tentativas.
    wait_time: Tempo de espera entre tentativas.
    """
    attempt = attempt + 1
    print(f"Tentativa {attempt}/{max_attempts} falhou devido a conexão")
    logging.info(f"Aguardando {wait_time} segundos antes de tentar novamente...")
    time.sleep(wait_time)
    return attempt


def log_success(start_time):
    """
    Registra o sucesso do download e o tempo total.
    """
    end_time = time.time()
    total_time = end_time - start_time
    minutes, seconds = divmod(total_time, 60)
    logging.info(f"Download concluído em {int(minutes)}m e {int(seconds)}s.")


def validate_response_status(response):
    """
    Lida com os diferentes status de resposta do servidor durante o download.
    Parâmetros:
    response: Objeto de resposta do request.
    """
    if response.status_code == 416:
        logging.info("Download já completo.")
        return
    elif response.status_code not in [206, 200]:
        raise requests.HTTPError("O servidor não suporta recomeço de downloads")


def download_with_progress(stream, output_path, filename, max_attempts=7, wait_time=5):
    url = stream.url
    global start_time
    attempts = 0
    while attempts < max_attempts:
        try:
            start_time = time.time()
            output_file = os.path.join(output_path, filename)
            # Verifica se o arquivo já existe e obtém o tamanho do arquivo
            file_size = os.path.getsize(output_file) if os.path.exists(output_file) else 0
            headers = {'Range': f'bytes={file_size}-'}
            response = requests.get(url, headers=headers, stream=True)
            validate_response_status(response)
            total_size = int(response.headers.get('content-range', f'bytes {file_size}-0').split('/')[1])
            block_size = 1024  # 1 KiB
            with tqdm(total=total_size, initial=file_size, unit='B', unit_scale=True, unit_divisor=1024, desc="Baixando") as progress_bar:
                with open(output_file, 'ab') as f:
                    for data in response.iter_content(block_size):
                        if data:
                            progress_bar.update(len(data))
                            f.write(data)
            log_success(start_time)
            return
        except (requests.ConnectionError, requests.Timeout):
            attempts = retry_download(attempt=attempts, max_attempts=max_attempts, wait_time=wait_time)
        except Exception as e:
            logging.error(f"Erro inesperado na tentativa {attempts + 1}/{max_attempts}: {e}")
            attempts = retry_download(attempt=attempts, max_attempts=max_attempts, wait_time=wait_time)
    print(f"Falha ao baixar o arquivo após {max_attempts} tentativas.")
    prompt_retry_download(url, output_path, filename)


def prompt_retry_download(url, output_path, filename):
    while True:
        choice = input("Deseja continuar o donwload? (s/n): ").strip().lower()
        if choice == 's':
            download_with_progress(url, output_path, filename)
            return
        elif choice == 'n':
            logging.error("Download cancelado pelo usuário.")
            sys.exit(0)
        else:
            print("Entrada inválida. Por favor, responda com 's' para SIM ou 'n' para NÃO.")


def confirm_file_overwrite(output_file, video_filename, output_path):
    """
    Verifica se o arquivo já existe no caminho especificado e solicita a confirmação do usuário para prosseguir com o download.

    Parâmetros:
    output_file (str): Caminho completo para o arquivo de saída.
    video_filename (str): Nome do arquivo de vídeo.
    output_path (str): Caminho de saída onde o arquivo está localizado.

    Retorna:
    None: Se o usuário escolher não prosseguir com o download.
    """
    if os.path.exists(output_file):
        print(f"\nArquivo '{video_filename}' já existe no caminho '{output_path}'.")
        while True:
            choice = input("Deseja baixar mesmo assim? (s/n): ").strip().lower()
            if choice == 's':
                return generate_unique_filename(output_path, video_filename)
            elif choice == 'n':
                return None
            else:
                print("Entrada inválida. Por favor, responda com 's' para SIM ou 'n' para NÃO.")
    return video_filename


def download_and_merge_video(youtube, title, selected_stream, output_path, video_extension, video_filename):
    global video_path, audio_path
    try:
        audio_stream = youtube.streams.filter(only_audio=True).order_by('abr').desc().first()
        if not audio_stream:
            logging.warning("Não foi possível encontrar um stream de áudio compatível.")
            return
        audio_filename = f"{title}_{audio_stream.abr}.{audio_stream.subtype}"
        video_path = os.path.join(output_path, f"{title}_temp.{video_extension}")
        audio_path = os.path.join(output_path, audio_filename)
        download_with_progress(selected_stream, output_path, f"{title}_temp.{video_extension}")
        print(f"\nFazendo download do áudio - {audio_stream.abr} kbps...")
        # logging.info(f"Fazendo download do áudio - {audio_stream.abr} kbps...")
        download_with_progress(audio_stream, output_path, audio_filename)
        codec_map = {
            'mp4': ('libx264', 'aac'),
            'webm': ('libvpx', 'libvorbis'),
            'mkv': ('libx264', 'aac')
        }
        if video_extension in codec_map:
            final_video_codec, final_audio_codec = codec_map[video_extension]
        else:
            logging.warning(f"Extensão {video_extension} não suportada.")
            return
        logging.info("Combinando vídeo e áudio...")
        # Usando gerenciador de contexto 'with' para garantir que os clips sejam fechados corretamente
        with VideoFileClip(video_path) as video_clip, AudioFileClip(audio_path) as audio_clip:
            final_clip = video_clip.set_audio(audio_clip)
            final_clip_path = os.path.join(output_path, video_filename)
            final_clip.write_videofile(final_clip_path, codec=final_video_codec, audio_codec=final_audio_codec)
        logging.info("Download e combinação concluídos!")
    except Exception as e:
        logging.error(f"Ocorreu um erro ao fazer download: {str(e)}")


def download_video(title, youtube, selected_stream, output_path='./'):
    global audio_path, video_path
    try:
        video_extension = selected_stream.mime_type.split('/')[1]
        video_filename = f"{title}.{video_extension}"
        output_file = os.path.join(output_path, video_filename)
        video_name = confirm_file_overwrite(output_file=output_file, output_path=output_path, video_filename=video_filename)
        if video_name is None:
            return
        video_filename = video_name
        logging.info(f"Fazendo download do vídeo '{title}' na resolução {selected_stream.resolution}")
        if selected_stream.includes_audio_track:
            download_with_progress(selected_stream, output_path, video_filename)
            logging.info("Download do vídeo com áudio integrado concluído!")
            return
        download_and_merge_video(youtube, title, selected_stream, output_path, video_extension, video_filename)
    except Exception as e:
        logging.error(f"Ocorreu um erro ao fazer download do vídeo: {str(e)}")


def main():
    try:
        print("Bem-vindo ao programa de download de vídeos do YouTube!")
        video_url = input("Insira o link do vídeo do YouTube: ").strip()
        title, streams, yt = get_video_details(video_url)
        if not streams:
            print("Não foi possível obter os streams do vídeo. Verifique o link e tente novamente.")
            return
        available_streams = retrieve_available_streams(streams)
        if not available_streams:
            print("Nenhuma resolução disponível para download.")
            return
        selected_stream = select_stream(streams=available_streams)
        output_folder = input(
            "Insira o caminho de saída para salvar o vídeo (pressione Enter para salvar na pasta de Downloads): ").strip()
        if output_folder.strip() == '':
            # Detecta a pasta de Downloads do usuário
            home = os.path.expanduser("~")
            output_folder = os.path.join(home, 'Downloads')
        download_video(youtube=yt, title=title, selected_stream=selected_stream, output_path=output_folder)
    except KeyboardInterrupt:
        logging.warning("Operação interrompida pelo usuário.")
        sys.exit(0)
    except Exception as e:
        logging.error(f"Ocorreu um erro inesperado: {str(e)}")
        sys.exit(1)
    finally:
        try:
            if 'video_path' in globals() and os.path.exists(video_path):
                os.remove(video_path)
                print("Arquivo temporário de vídeo removido com sucesso.")
            if 'audio_path' in globals() and os.path.exists(audio_path):
                os.remove(audio_path)
                print("Arquivo temporário de áudio removido com sucesso.")
        except OSError as e:
            logging.error(f"Erro ao remover arquivos temporários")
            raise OSError(f"Não foi possível remover todos os arquivos temporários: {str(e)}")
        logging.warning("Encerrando o programa de download de vídeos do YouTube...")


def select_stream(streams):
    while True:
        try:
            print("Streams disponíveis:")
            for idx, (stream_info, _) in enumerate(streams, 1):
                print(f"{idx} - {stream_info}")
            choice = int(input("Escolha a opção desejada (digite o número correspondente): ").strip())
            if 1 <= choice <= len(streams):
                selected_stream = streams[choice - 1][1]
                return selected_stream
            logging.warning("Escolha fora do intervalo disponível. Tente novamente.")
        except ValueError:
            print("Entrada inválida. Por favor, insira um número válido.")


def generate_unique_filename(output_folder, base_filename):
    base_name, extension = os.path.splitext(base_filename)  # Separa o nome do arquivo e a extensão
    index = 1
    while True:
        candidate_name = f"{base_name}({index}){extension}"
        candidate_path = os.path.join(output_folder, candidate_name)
        if not os.path.exists(candidate_path):
            return candidate_name
        index += 1


if __name__ == "__main__":
    main()
