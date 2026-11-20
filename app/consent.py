"""Consent text for participants (Singapore first market: English, Chinese, Malay, Tamil).

DRAFT templates. Malay and Tamil are machine-drafted: they MUST be checked by a native speaker and by counsel
before any real participant sees them. Wording follows PDPA Singapore basics (purpose, overseas transfer, retention,
withdrawal, a contact for questions) plus separate consent for the China transfer.
"""
import hashlib

from .config import settings
from .moderator.compliance import moderator_runs_locally

VERSION = "2026-11-draft-11"  # draft-11: the same points written as short bullet points under four headings (用户 2026-11-20: 清楚明了，简单); the AI disclosure is the first section; draft-10: in an AI audio / video interview the AI speaks with a synthesized voice and the participant holds the mic to answer (用户 2026-11-20: 写“合成语音”，不写服务商); draft-9: voice input on the participant pages (the voice goes to Tencent Cloud speech recognition to become text, not kept); draft-8: the number of days before deletion is the project's own choice (30/60/120/180); draft-7: studies done alone, in text, at any time have their own paragraph; draft-6: how things are said (pace, pauses, volume) and emoji are noted; draft-5: the AI also reads the screening answers; draft-4: AI-moderator disclosure (AI interviews only)

# 修正方案第 4 轮 (用户 2026-11-20: 写得清楚明了，简单): the same points as draft-10, written as short bullet points under four headings.
# Every line that starts with "• " is a point; the other lines are headings. Malay and Tamil are first drafts to be checked by native speakers and counsel.
HEADS = {
    "en": ("About this interview", "What we record", "Where your data goes", "Your rights"),
    "zh": ("关于这次访谈", "我们会记录什么", "数据去哪里", "您的权利"),
    "ms": ("Tentang temu bual ini", "Apa yang kami rakam", "Ke mana data anda pergi", "Hak anda"),
    "ta": ("இந்த நேர்காணல் பற்றி", "நாங்கள் பதிவு செய்வது", "உங்கள் தரவு எங்கே செல்கிறது", "உங்கள் உரிமைகள்"),
}
WHO = {
    "en": "TOTAL runs this interview for {org}, for market research.",
    "zh": "本次访谈由星桐代 {org} 进行，用于市场研究。",
    "ms": "TOTAL menjalankan temu bual ini bagi pihak {org}, untuk kajian pasaran.",
    "ta": "சந்தை ஆய்வுக்காக {org} சார்பாக TOTAL இந்த நேர்காணலை நடத்துகிறது.",
}
OBSERVERS = {
    "en": "Client observers may listen silently; you will not see them.",
    "zh": "委托方的观察员可能静默旁听，您看不到他们。",
    "ms": "Pemerhati klien mungkin mendengar secara senyap; anda tidak akan melihat mereka.",
    "ta": "வாடிக்கையாளர் பார்வையாளர்கள் அமைதியாகக் கேட்கலாம்; நீங்கள் அவர்களைப் பார்க்க முடியாது.",
}
# A study done alone: replaces the picture of a live video call.
TEXT_ONLY_TEXTS = {
    "en": "This is a written conversation you complete on your own, at any time. No video or sound is recorded — what you type is the record; the team reads it afterwards.",
    "zh": "这是一场文字对话，您自己在方便的时间完成。不录像、不录音，您输入的文字就是记录，研究团队事后阅读。",
    "ms": "Ini perbualan bertulis yang anda selesaikan sendiri, pada bila-bila masa. Tiada video atau bunyi dirakam — apa yang anda taip ialah rekod; pasukan membacanya kemudian.",
    "ta": "இது நீங்களே எந்த நேரத்திலும் முடிக்கும் எழுத்து உரையாடல். வீடியோ, ஒலி பதிவு இல்லை — நீங்கள் தட்டச்சு செய்வதே பதிவு; குழு பின்னர் படிக்கும்.",
}
RECORD = {
    "en": "Video and sound are recorded, turned into text and translated into Chinese.",
    "zh": "录制视频和声音，转写成文字并翻译成中文。",
    "ms": "Video dan bunyi dirakam, ditukar menjadi teks dan diterjemahkan ke bahasa Cina.",
    "ta": "காணொளியும் ஒலியும் பதிவு செய்யப்பட்டு, எழுத்தாக மாற்றப்பட்டு, சீன மொழியில் மொழிபெயர்க்கப்படும்.",
}
RECORD_TEXT = {
    "en": "Your typed answers are kept and translated into Chinese.",
    "zh": "保存您输入的文字，并翻译成中文。",
    "ms": "Jawapan yang anda taip disimpan dan diterjemahkan ke bahasa Cina.",
    "ta": "நீங்கள் தட்டச்சு செய்த பதில்கள் சேமிக்கப்பட்டு சீன மொழியில் மொழிபெயர்க்கப்படும்.",
}
# How things are said, and emoji (the live captions are always on): numbers and the participant's own emoji only.
SIGNALS_TEXTS = {
    "en": "We note your speaking pace, pauses and volume (compared only with your own usual way of speaking) and any emoji you send — not to judge your feelings.",
    "zh": "记录您说话的语速、停顿和音量（只和您自己平时比较）以及您发的表情，不用来判断您的情绪。",
    "ms": "Kami mencatat kelajuan, jeda dan kelantangan anda bercakap (dibandingkan hanya dengan cara biasa anda) dan emoji yang anda hantar — bukan untuk menilai perasaan anda.",
    "ta": "உங்கள் பேச்சின் வேகம், இடைநிறுத்தம், ஒலி அளவு (உங்கள் வழக்கத்துடன் மட்டும் ஒப்பிட்டு) மற்றும் நீங்கள் அனுப்பும் எமோஜிகளைக் குறிக்கிறோம் — உங்கள் உணர்வுகளை மதிப்பிட அல்ல.",
}
VOICE_TEXTS = {   # 修正方案第 1 轮第 2 条: the microphone on the participant pages
    "en": "If you speak instead of typing, your voice goes to Tencent Cloud speech recognition only to become text; the recording is not kept.",
    "zh": "用说话输入时，声音只用来转成文字（发给腾讯云语音识别），录音不保存。",
    "ms": "Jika anda bercakap dan bukan menaip, suara anda dihantar kepada pengecaman pertuturan Tencent Cloud hanya untuk menjadi teks; rakaman tidak disimpan.",
    "ta": "தட்டச்சுக்குப் பதில் பேசினால், உங்கள் குரல் உரையாக மாற மட்டுமே Tencent Cloud பேச்சு அறிதலுக்கு அனுப்பப்படும்; பதிவு சேமிக்கப்படாது.",
}
STORE = {
    "en": "Stored on secure servers in Singapore and accessed by our team in China, with protections comparable to Singapore's.",
    "zh": "存放在新加坡的安全服务器上，由我们在中国的团队访问，保护措施与新加坡要求相当。",
    "ms": "Disimpan di pelayan selamat di Singapura dan diakses oleh pasukan kami di China, dengan perlindungan setanding dengan keperluan Singapura.",
    "ta": "சிங்கப்பூரில் உள்ள பாதுகாப்பான சேவையகங்களில் சேமிக்கப்பட்டு, சீனாவில் உள்ள எங்கள் குழுவால் அணுகப்படும்; சிங்கப்பூருக்கு இணையான பாதுகாப்புடன்.",
}
DELETE = {
    "en": "Deleted {days} days after the research project ends.",
    "zh": "研究项目结束 {days} 天后删除。",
    "ms": "Dipadam {days} hari selepas projek kajian tamat.",
    "ta": "ஆய்வுத் திட்டம் முடிந்து {days} நாட்களுக்குப் பிறகு நீக்கப்படும்.",
}
NO_SHARE = {
    "en": "We will not give your name or contact details to the client without your separate consent.",
    "zh": "未经您另行同意，不会把您的姓名或联系方式提供给委托方。",
    "ms": "Kami tidak akan memberi nama atau butiran hubungan anda kepada klien tanpa persetujuan berasingan anda.",
    "ta": "உங்கள் தனி ஒப்புதல் இல்லாமல் உங்கள் பெயரையோ தொடர்பு விவரங்களையோ வாடிக்கையாளருக்குத் தர மாட்டோம்.",
}
RIGHTS = {
    "en": "You can withdraw your consent, or ask to see, correct or delete your data, at any time: write to {dpo}.",
    "zh": "您可以随时撤回同意，或要求查阅、更正、删除您的数据：联系 {dpo}。",
    "ms": "Anda boleh menarik balik persetujuan, atau meminta untuk melihat, membetulkan atau memadam data anda, pada bila-bila masa: tulis kepada {dpo}.",
    "ta": "எந்த நேரத்திலும் ஒப்புதலைத் திரும்பப் பெறலாம், அல்லது உங்கள் தரவைப் பார்க்க, திருத்த, நீக்கக் கேட்கலாம்: {dpo} க்கு எழுதுங்கள்.",
}
LABELS = {
    "en": {"recording": "I agree to video and audio recording", "observers": "I understand client observers may listen silently",
           "cross_border": "I agree to my data being stored in Singapore and accessed by the team in China (separate consent)"},
    "zh": {"recording": "我同意录制我的视频和声音", "observers": "我知道委托方观察员可能静默旁听", "cross_border": "我同意我的信息存放在新加坡，并由中国团队访问（单独同意）"},
    "ms": {"recording": "Saya bersetuju video dan audio saya dirakam", "observers": "Saya faham pemerhati klien mungkin mendengar secara senyap",
           "cross_border": "Saya bersetuju maklumat saya disimpan di Singapura dan diakses oleh pasukan di China (persetujuan berasingan)"},
    "ta": {"recording": "என் காணொளி மற்றும் ஒலி பதிவு செய்யப்படுவதற்கு நான் ஒப்புக்கொள்கிறேன்",
           "observers": "வாடிக்கையாளர் பார்வையாளர்கள் அமைதியாகக் கேட்கக்கூடும் என்பதை நான் புரிந்துகொள்கிறேன்",
           "cross_border": "என் தகவல் சிங்கப்பூரில் சேமிக்கப்பட்டு, சீனாவில் உள்ள குழுவால் அணுகப்படுவதற்கு நான் ஒப்புக்கொள்கிறேன் (தனி ஒப்புதல்)"},
}
TEXTS = WHO   # (older code imported TEXTS to know which languages exist)

# Only for interviews run by the AI moderator: it must be disclosed first (red line H3). A human-moderated interview never shows it.
AI_TEXTS = {
    "en": "This interview is run by an AI interviewer, not a person. Our research team can see the conversation and may step in at any time.",
    "zh": "本次访谈由 AI 主持人提问，不是真人；研究团队能看到对话，可随时接手。",
    "ms": "Temu bual ini dikendalikan oleh pewawancara AI, bukan manusia. Pasukan kajian kami boleh melihat perbualan dan boleh mengambil alih pada bila-bila masa.",
    "ta": "இந்த நேர்காணலை ஒரு மனிதர் அல்ல, AI நேர்காணலாளர் நடத்துகிறது. எங்கள் ஆய்வுக் குழு உரையாடலைப் பார்க்கலாம், எந்த நேரத்திலும் பொறுப்பேற்கலாம்.",
}
AI_SAFE = {
    "en": "The AI never asks for your name, phone number or ID, and you can ask to stop at any time.",
    "zh": "AI 不会问您的姓名、电话或证件；您可以随时要求停止。",
    "ms": "AI tidak akan meminta nama, nombor telefon atau dokumen pengenalan anda, dan anda boleh meminta untuk berhenti pada bila-bila masa.",
    "ta": "AI உங்கள் பெயர், தொலைபேசி எண் அல்லது அடையாள ஆவணத்தைக் கேட்காது; எந்த நேரத்திலும் நிறுத்தச் சொல்லலாம்.",
}
# 修正方案第 3 轮: an audio / video AI interview is spoken (synthesized voice, no provider named — the user's decision).
AI_VOICE = {
    "en": "In an audio or video interview, the AI speaks in a synthesized voice; hold the microphone button to answer and let go to send.",
    "zh": "语音或视频访谈中，AI 用合成语音说话；您按住麦克风说话，松开就发送。",
    "ms": "Dalam temu bual audio atau video, AI bercakap dengan suara sintetik; tekan dan tahan butang mikrofon untuk menjawab dan lepaskan untuk menghantar.",
    "ta": "ஒலி அல்லது காணொளி நேர்காணலில் AI செயற்கைக் குரலில் பேசும்; பதில் சொல்ல மைக் பொத்தானை அழுத்திப் பிடித்து, அனுப்ப விடுங்கள்.",
}
# The screening answers are background for the AI (it must not ask again what was already answered).
AI_PROFILE = {
    "en": "The AI can see your earlier screening answers, so it does not ask the same things again.",
    "zh": "AI 能看到您之前的甄别问卷答案，以免重复询问。",
    "ms": "AI boleh melihat jawapan saringan anda sebelum ini supaya tidak bertanya perkara yang sama lagi.",
    "ta": "நீங்கள் முன்பு அளித்த தேர்வுப் பதில்களை AI பார்க்கலாம்; அதனால் ஒரே கேள்விகளை மீண்டும் கேட்காது.",
}
# Where the AI runs. A cloud model is one more transfer of the participant's words, so the provider is named (task doc part 3).
AI_LOCAL = {
    "en": "The AI runs on our own servers; your answers are not sent to any other company for it.",
    "zh": "AI 运行在我们自己的服务器上，您的回答不会为此发给其他公司。",
    "ms": "AI berjalan pada pelayan kami sendiri; jawapan anda tidak dihantar kepada syarikat lain untuknya.",
    "ta": "AI எங்கள் சொந்த சேவையகங்களில் இயங்குகிறது; இதற்காக உங்கள் பதில்கள் வேறு நிறுவனத்துக்கு அனுப்பப்படாது.",
}
AI_CLOUD = {
    "en": "To run the AI, your answers are sent to the AI service provider {vendor}, which handles them for this interview only.",
    "zh": "为运行 AI，您的回答会发给 AI 服务商 {vendor}，仅为本次访谈处理。",
    "ms": "Untuk menjalankan AI, jawapan anda dihantar kepada pembekal perkhidmatan AI {vendor}, yang mengendalikannya untuk temu bual ini sahaja.",
    "ta": "AI-ஐ இயக்க, உங்கள் பதில்கள் AI சேவை வழங்குநர் {vendor}-க்கு அனுப்பப்படும்; இந்த நேர்காணலுக்கு மட்டுமே கையாளப்படும்.",
}
AI_LABELS = {
    "en": "I understand the interviewer is an AI, not a person",
    "zh": "我知道本次访谈由 AI 主持，不是真人",
    "ms": "Saya faham pewawancara ialah AI, bukan manusia",
    "ta": "நேர்காணலாளர் மனிதர் அல்ல, AI என்பதை நான் புரிந்துகொள்கிறேன்",
}


def _where(lang: str) -> str:
    return AI_LOCAL[lang] if moderator_runs_locally() else AI_CLOUD[lang].format(vendor=settings.moderator_vendor.strip())


def consent_for(lang: str, org_name: str, ai: bool = False, text_only: bool = False, retention_days: int = 180) -> dict:
    """The consent text a participant agrees to, and the checkbox labels.

    Args:
        lang: interview language; anything outside en/zh/ms/ta falls back to English.
        org_name: the client organisation named in the text.
        ai: True for an AI-moderated interview: the AI disclosure comes first and an extra "ai" checkbox is required.
        retention_days: days after the project ends until the data is deleted (the project's choice; at most 180).
        text_only: a study done alone, in writing, at any time (no video or sound; the team reads the conversation afterwards): says so first.

    Returns:
        {"version", "lang", "text", "labels", "ai", "sha256"}; sha256 is stored with the consent so the exact text can be proven later.
    """
    l = "zh" if (lang or "").startswith("zh") else lang if lang in WHO else "en"  # anything else: English
    h = HEADS[l]
    about = ([AI_TEXTS[l], AI_SAFE[l], AI_VOICE[l]] if ai else []) + ([TEXT_ONLY_TEXTS[l]] if text_only else []) + [WHO[l].format(org=org_name), OBSERVERS[l]]
    record = [RECORD_TEXT[l] if text_only else RECORD[l], SIGNALS_TEXTS[l], VOICE_TEXTS[l]] + ([AI_PROFILE[l]] if ai else [])
    where = ([_where(l)] if ai else []) + [STORE[l], DELETE[l].format(days=retention_days)]
    rights = [NO_SHARE[l], RIGHTS[l].format(dpo=settings.dpo_email)]
    text = "\n".join(f"{head}\n" + "\n".join("• " + x for x in points) for head, points in zip(h, (about, record, where, rights)))
    labels = dict(LABELS[l])
    if ai:
        labels = {"ai": AI_LABELS[l], **labels}
    return {"version": VERSION, "lang": l, "text": text, "labels": labels, "ai": ai, "sha256": hashlib.sha256(text.encode()).hexdigest()}
