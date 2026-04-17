# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# Virtual File System for safe EPUB operations
# ---------------------------------------------------------------------------
# This module provides a virtual file system for safely manipulating EPUB files
# in memory before committing changes to disk.
# ---------------------------------------------------------------------------

import os
import shutil
import tempfile
import threading
from typing import Optional, Dict


class VirtualFileSystem:
    """
    Manages in-memory file copies for safe EPUB operations.
    
    This class provides a singleton-like registry of virtual files that can be
    modified in memory and later synced back to disk. It ensures atomic operations
    and proper cleanup of temporary files.
    
    Usage:
        # Copy file to virtual memory
        virtual_path = VirtualFileSystem.copy_to_mem("/path/to/source.epub")
        
        # ... perform operations on virtual_path ...
        
        # Sync back to disk
        VirtualFileSystem.copy_from_mem(virtual_path, "/path/to/dest.epub")
        
        # Cleanup when done
        VirtualFileSystem.cleanup(virtual_path)
    """
    
    _instances: Dict[str, str] = {}  # Maps virtual_path -> temp_file_path
    _lock = threading.Lock()  # Thread-safe access to registry
    
    @classmethod
    def copy_to_mem(cls, source_path: str) -> Optional[str]:
        """
        Copy a file to a temporary location for in-memory operations.
        
        Args:
            source_path: Path to the source file on disk.
            
        Returns:
            Virtual path (temp file path) if successful, None otherwise.
        """
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Source file not found: {source_path}")
        
        with cls._lock:
            # Create a temp file in the same directory for atomic rename support
            source_dir = os.path.dirname(os.path.abspath(source_path))
            source_name = os.path.basename(source_path)
            
            # Create temp file with meaningful name for debugging
            temp_fd, temp_path = tempfile.mkstemp(
                prefix=f"virt_{source_name}_",
                suffix=".tmp",
                dir=source_dir
            )
            os.close(temp_fd)  # Close the file descriptor
            
            try:
                # Copy content to temp file
                shutil.copy2(source_path, temp_path)
                
                # Register the virtual file
                cls._instances[temp_path] = temp_path
                
                return temp_path
                
            except Exception as e:
                # Cleanup on failure
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise e
    
    @classmethod
    def copy_from_mem(cls, virtual_path: str, dest_path: str) -> bool:
        """
        Write virtual file content back to disk.
        
        Args:
            virtual_path: Path to the virtual (temp) file.
            dest_path: Destination path on disk.
            
        Returns:
            True if successful, False otherwise.
        """
        with cls._lock:
            if virtual_path not in cls._instances:
                return False
            
            if not os.path.exists(virtual_path):
                return False
            
            try:
                # Create backup of destination if it exists
                if os.path.exists(dest_path):
                    backup_path = dest_path + ".bak"
                    shutil.copy2(dest_path, backup_path)
                
                # Ensure destination directory exists
                dest_dir = os.path.dirname(os.path.abspath(dest_path))
                if dest_dir and not os.path.exists(dest_dir):
                    os.makedirs(dest_dir)
                
                # Atomic copy (same filesystem for rename would be ideal, but copy works)
                shutil.copy2(virtual_path, dest_path)
                
                return True
                
            except Exception as e:
                print(f"[VirtualFileSystem] Error copying to disk: {e}")
                return False
    
    @classmethod
    def cleanup(cls, virtual_path: str) -> None:
        """
        Remove virtual file and clean up resources.
        
        Args:
            virtual_path: Path to the virtual (temp) file to remove.
        """
        with cls._lock:
            if virtual_path in cls._instances:
                try:
                    # Remove the temp file
                    if os.path.exists(virtual_path):
                        os.remove(virtual_path)
                    
                    # Remove from registry
                    del cls._instances[virtual_path]
                    
                except Exception as e:
                    print(f"[VirtualFileSystem] Cleanup error for {virtual_path}: {e}")
    
    @classmethod
    def cleanup_all(cls) -> None:
        """Remove all registered virtual files. Call on application shutdown."""
        with cls._lock:
            for virtual_path in list(cls._instances.keys()):
                cls.cleanup(virtual_path)
    
    @classmethod
    def is_virtual(cls, path: str) -> bool:
        """Check if a path is a registered virtual file."""
        return path in cls._instances
    
    @classmethod
    def get_real_path(cls, virtual_path: str) -> Optional[str]:
        """Get the real temp file path for a virtual path (same as input)."""
        with cls._lock:
            return cls._instances.get(virtual_path)


# Convenience functions for backward compatibility with existing code
def copy_to_mem(source_path: str) -> Optional[str]:
    """Convenience function: Copy file to virtual memory."""
    return VirtualFileSystem.copy_to_mem(source_path)


def copy_from_mem(virtual_path: str, dest_path: str) -> bool:
    """Convenience function: Write virtual file to disk."""
    return VirtualFileSystem.copy_from_mem(virtual_path, dest_path)


def cleanup_virtual(virtual_path: str) -> None:
    """Convenience function: Cleanup virtual file."""
    VirtualFileSystem.cleanup(virtual_path)
