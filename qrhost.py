import http.server
import socketserver
import os
import sys
import qrcode
import socket
import netifaces
from urllib.parse import quote
import mimetypes
import re

PORT = 6969  

def get_ip():
    try:
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface).get(netifaces.AF_INET)
            if addrs:
                for addr in addrs:
                    ip = addr['addr']
                    if not ip.startswith("127."):
                        return ip
    except Exception:
        pass
    return socket.gethostbyname(socket.gethostname())

def generate_qr(url):
    qr = qrcode.QRCode()
    qr.add_data(url)
    qr.make()
    qr.print_ascii(invert=True)
    print(f"\nDownload URL: {url}\n")

def serve_directory(directory):
    os.chdir(directory)
    url_path = quote(directory)
    ip = get_ip()
    download_url = f"http://{ip}:{PORT}/{url_path}/"
    generate_qr(download_url)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving directory '{directory}' at http://localhost:{PORT}/ (Press Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
            httpd.shutdown()
            httpd.server_close()

def serve_file(filepath):
    directory, filename = os.path.split(os.path.abspath(filepath))
    os.chdir(directory)

    url_path = quote(filename)
    ip = get_ip()
    download_url = f"http://{ip}:{PORT}/{url_path}"
    generate_qr(download_url)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            
            if self.path.endswith('.txt'):
                self.send_header('Content-Disposition', 'attachment; filename=' + os.path.basename(self.path))
            super().end_headers()

        def log_message(self, format, *args):
            pass  

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving '{filename}' at http://localhost:{PORT}/ (Press Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
            httpd.shutdown()
            httpd.server_close()

def is_valid_url(url):
   
    url_regex = re.compile(
        r'^(?:http|ftp)s?://' 
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  
        r'localhost|'  
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|'  
        r'\[?[A-F0-9]*:[A-F0-9:]+\]?)'  
        r'(?::\d+)?'  
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return re.match(url_regex, url) is not None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python file_host_qr.py <file_or_directory_or_url>")
        sys.exit(1)
    
    path = sys.argv[1]
    
    if is_valid_url(path):
        
        print("Serving URL:", path)
        generate_qr(path)
        print(f"Visit the above URL directly.")
    elif not os.path.exists(path):
        print(f"Error: Path not found: {path}")
        sys.exit(1)
    elif os.path.isdir(path):
        serve_directory(path)  
    elif os.path.isfile(path):
        serve_file(path)  
    else:
        print(f"Error: Unsupported path type: {path}")
        sys.exit(1)
