"""
Basic listener to read the UDP packet and convert it to a known packet format.
Now includes functionality to save data in a replayable format.
"""

import socket
import struct
import time
import os
from datetime import datetime
from typing import Optional, BinaryIO
from pathlib import Path

from f1_telemetry.packets import PacketHeader, HEADER_FIELD_TO_PACKET_TYPE


class TelemetryListener:
    def __init__(self, host: Optional[str] = None, port: Optional[int] = None, 
                 save_to_file: bool = False, save_directory: str = 'telemetry_data',
                 replay_file: Optional[str] = None):
        # Set to default port used by the game in telemetry setup.
        if not port:
            port = 20777

        if not host:
            host = '0.0.0.0'

        self.replay_mode = replay_file is not None
        self.save_to_file = save_to_file
        self.save_directory = Path(save_directory)
        self.start_time = None
        
        if self.replay_mode:
            # Replay mode - read from file instead of socket
            self.replay_file_handle = open(replay_file, 'rb')
            self.replay_start_time = time.time()
            self.socket = None
        else:
            # Live mode - create socket
            self.socket = socket.socket(family=socket.AF_INET, type=socket.SOCK_DGRAM)
            self.socket.bind((host, port))
            self.replay_file_handle = None
            
            # Setup file saving if enabled
            if self.save_to_file:
                self.save_directory.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                self.save_file_path = self.save_directory / f"telemetry_replay_{timestamp}.tlm"
                self.save_file_handle = open(self.save_file_path, 'wb')
                self.start_time = time.time()
            else:
                self.save_file_handle = None

    def get(self):
        if self.replay_mode:
            return self._get_from_replay()
        else:
            return self._get_from_socket()
    
    def _get_from_socket(self):
        """Get packet from live UDP socket."""
        packet = self.socket.recv(2048)
        current_time = time.time()
        
        # Save to replay file if enabled
        if self.save_to_file and self.save_file_handle:
            if self.start_time is None:
                self.start_time = current_time
            
            # Calculate relative timestamp
            relative_time = current_time - self.start_time
            self._write_packet_to_file(packet, relative_time)
        
        # Parse and return packet
        header = PacketHeader.from_buffer_copy(packet)
        key = (header.packet_format, header.packet_version, header.packet_id)
        return HEADER_FIELD_TO_PACKET_TYPE[key].unpack(packet)
    
    def _get_from_replay(self):
        """Get packet from replay file with proper timing."""
        if not self.replay_file_handle:
            raise RuntimeError("Replay file not open")
        
        try:
            # Read packet header from file
            header_data = self.replay_file_handle.read(12)  # 8 bytes timestamp + 4 bytes length
            if len(header_data) < 12:
                raise EOFError("End of replay file")
            
            # Unpack timestamp and packet length
            timestamp, packet_length = struct.unpack('<dI', header_data)
            
            # Read the actual packet data
            packet_data = self.replay_file_handle.read(packet_length)
            if len(packet_data) < packet_length:
                raise EOFError("Incomplete packet in replay file")
            
            # Calculate timing for replay
            current_replay_time = time.time() - self.replay_start_time
            if timestamp > current_replay_time:
                # Sleep to maintain original timing
                time.sleep(timestamp - current_replay_time)
            
            # Parse and return packet
            header = PacketHeader.from_buffer_copy(packet_data)
            key = (header.packet_format, header.packet_version, header.packet_id)
            return HEADER_FIELD_TO_PACKET_TYPE[key].unpack(packet_data)
            
        except EOFError:
            raise StopIteration("End of replay file reached")
    
    def _write_packet_to_file(self, packet: bytes, timestamp: float):
        """Write packet to replay file with timestamp."""
        # File format: [timestamp (8 bytes double)] [packet_length (4 bytes uint)] [packet_data]
        packet_length = len(packet)
        header = struct.pack('<dI', timestamp, packet_length)
        self.save_file_handle.write(header + packet)
        self.save_file_handle.flush()  # Ensure data is written immediately
    
    # def get_replay_info(self) -> dict:
    #     """Get information about the replay file."""
    #     if not self.replay_mode:
    #         return {"error": "Not in replay mode"}
        
    #     # Save current position
    #     current_pos = self.replay_file_handle.tell()
        
    #     # Go to beginning and scan file
    #     self.replay_file_handle.seek(0)
    #     packet_count = 0
    #     first_timestamp = None
    #     last_timestamp = None
        
    #     try:
    #         while True:
    #             header_data = self.replay_file_handle.read(12)
    #             if len(header_data) < 12:
    #                 break
                
    #             timestamp, packet_length = struct.unpack('<dI', header_data)
                
    #             if first_timestamp is None:
    #                 first_timestamp = timestamp
    #             last_timestamp = timestamp
                
    #             # Skip packet data
    #             self.replay_file_handle.seek(packet_length, 1)
    #             packet_count += 1
                
    #     except Exception:
    #         pass
        
    #     # Restore position
    #     self.replay_file_handle.seek(current_pos)
        
    #     duration = (last_timestamp - first_timestamp) if first_timestamp is not None else 0
        
    #     return {
    #         "packet_count": packet_count,
    #         "duration_seconds": duration,
    #         "first_timestamp": first_timestamp,
    #         "last_timestamp": last_timestamp
    #     }
    
    # def seek_to_time(self, target_time: float):
    #     """Seek to a specific time in the replay file."""
    #     if not self.replay_mode:
    #         raise RuntimeError("Not in replay mode")
        
    #     self.replay_file_handle.seek(0)
        
    #     try:
    #         while True:
    #             pos = self.replay_file_handle.tell()
    #             header_data = self.replay_file_handle.read(12)
    #             if len(header_data) < 12:
    #                 break
                
    #             timestamp, packet_length = struct.unpack('<dI', header_data)
                
    #             if timestamp >= target_time:
    #                 # Go back to this packet
    #                 self.replay_file_handle.seek(pos)
    #                 self.replay_start_time = time.time() - timestamp
    #                 return
                
    #             # Skip packet data
    #             self.replay_file_handle.seek(packet_length, 1)
                
    #     except Exception:
    #         pass
    
    def close(self):
        """Close the socket/file handles."""
        if self.socket:
            self.socket.close()
        if self.save_file_handle:
            self.save_file_handle.close()
        if self.replay_file_handle:
            self.replay_file_handle.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def __iter__(self):
        """Make the listener iterable for replay mode."""
        return self
    
    def __next__(self):
        """Support iteration in replay mode."""
        try:
            return self.get()
        except StopIteration:
            raise
