import NDIlib as ndi
import numpy as np
import pygame
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
import threading

# --- KONFIGURACJA ---
OSC_IP = "0.0.0.0"
OSC_PORT = 8010
WIDTH, HEIGHT = 1792, 2400


class NDIReceiver:
    def __init__(self):
        pygame.init()
        # Ustawiamy flagę SCALED, jeśli okno jest za duże na Twój monitor
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
        pygame.display.set_caption("NDI Receiver - Stream A/B")

        self.opacities = {"/surfaces/Quad-1/opacity": 0.0, "/surfaces/Quad-2/opacity": 0.0}
        self.frames = [None, None]

        self.dispatcher = Dispatcher()
        self.dispatcher.map("/surfaces/Quad-1/opacity", self.set_opacity)
        self.dispatcher.map("/surfaces/Quad-2/opacity", self.set_opacity)

        self.osc_thread = threading.Thread(target=self.run_osc, daemon=True)
        self.osc_thread.start()

        self.ndi_find = ndi.find_create_v2()
        self.receivers = [None, None]
        self.names = ["Stream_A", "Stream_B"]

    def set_opacity(self, address, *args):
        # args[0] to zazwyczaj wartość z OSC
        if args:
            self.opacities[address] = float(args[0])

    def run_osc(self):
        try:
            server = ThreadingOSCUDPServer((OSC_IP, OSC_PORT), self.dispatcher)
            print(f"[*] OSC Server aktywny: {OSC_IP}:{OSC_PORT}")
            server.serve_forever()
        except Exception as e:
            print(f"[!] Błąd serwera OSC: {e}")

    def connect_ndi(self):
        print("[*] Szukanie źródeł NDI...")
        while not all(self.receivers):
            ndi.find_wait_for_sources(self.ndi_find, 1000)
            sources = ndi.find_get_current_sources(self.ndi_find)

            for s in sources:
                for i, name in enumerate(self.names):
                    if name in s.ndi_name and self.receivers[i] is None:
                        print(f"[+] Podpięto {name} pod odbiornik {i}")
                        cfg = ndi.RecvCreateV3()
                        cfg.color_format = ndi.RECV_COLOR_FORMAT_BGRX_BGRA
                        self.receivers[i] = ndi.recv_create_v3(cfg)
                        ndi.recv_connect(self.receivers[i], s)

            if not all(self.receivers):
                print("...czekam na oba streamy (A i B)...")

    def run(self):
        self.connect_ndi()
        clock = pygame.time.Clock()
        running = True

        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            for i in range(2):
                if self.receivers[i]:
                    t, v, _, _ = ndi.recv_capture_v2(self.receivers[i], 0)
                    if t == ndi.FRAME_TYPE_VIDEO:
                        frame = np.copy(v.data)
                        surface = pygame.image.frombuffer(frame, (v.xres, v.yres), "BGRA")
                        self.frames[i] = surface.convert_alpha()
                        ndi.recv_free_video_v2(self.receivers[i], v)

            self.screen.fill((0, 0, 0))

            for i, addr in enumerate(["/surfaces/Quad-1/opacity", "/surfaces/Quad-2/opacity"]):
                if self.frames[i]:
                    alpha = int(self.opacities[addr] * 255)
                    self.frames[i].set_alpha(alpha)
                    self.screen.blit(self.frames[i], (0, 0))

            pygame.display.flip()
            clock.tick(60)

        pygame.quit()


if __name__ == "__main__":
    receiver = NDIReceiver()
    receiver.run()