import asyncio, subprocess
from pathlib import Path
from curl_cffi.requests import AsyncSession

async def test():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Referer': 'https://dcdlc9f2bddfff97.xyz/player/mh_gHP9c7SOTjn3x5S29Gfk9'
    }
    async with AsyncSession(impersonate='chrome124') as s:
        # Video
        rv = await s.get('https://redirect.centralodsseyproud.click/1w75zQ7qowmTuQZu0CUP4dvtm9BhzCIV/nPiM2a/2SvD1wvK3fYMmda', headers=headers)
        Path('/root/dizibot/data/temp/t_v.ts').write_bytes(rv.content)
        
        # Audio
        ra = await s.get('https://one.82b6b2a6748f1e.click/mh_gHP9c7SOTjn3x5S29Gfk9/turkish/playlist.m3u8', headers=headers)
        a_seg = [l.strip() for l in ra.text.splitlines() if l.strip() and not l.startswith('#')][0]
        ras = await s.get(a_seg, headers=headers)
        Path('/root/dizibot/data/temp/t_a.ts').write_bytes(ras.content)
        
        # Subtitle
        rs = await s.get('https://one.82b6b2a6748f1e.click/mh_gHP9c7SOTjn3x5S29Gfk9/sub_turkish/turkish.vtt', headers=headers)
        Path('/root/dizibot/data/temp/t_s.vtt').write_bytes(rs.content)
        
        cmd = [
            "ffmpeg", "-y", "-threads", "0", "-fflags", "+genpts+discardcorrupt",
            "-i", "/root/dizibot/data/temp/t_v.ts",
            "-i", "/root/dizibot/data/temp/t_a.ts",
            "-i", "/root/dizibot/data/temp/t_s.vtt",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-map", "2:s:0?",
            "-c:v", "copy",
            "-c:a", "copy",
            "-c:s", "mov_text",
            "-bsf:a", "aac_adtstoasc",
            "-avoid_negative_ts", "make_zero",
            "-shortest",
            "-movflags", "+faststart",
            "/root/dizibot/data/temp/t_out.mp4"
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        print("Exit code:", p.returncode)
        if p.returncode != 0:
            print("STDERR:\n", p.stderr)
        else:
            print("SUCCESS! Output size:", Path("/root/dizibot/data/temp/t_out.mp4").stat().st_size)

asyncio.run(test())
