"""Consent text for participants (Singapore first market: English, Chinese, Malay, Tamil).

DRAFT templates. Malay and Tamil are machine-drafted: they MUST be checked by a native speaker and by counsel
before any real participant sees them. Wording follows PDPA Singapore basics (purpose, overseas transfer, retention,
withdrawal, a contact for questions) plus separate consent for the China transfer.
"""
import hashlib

from .config import settings
from .moderator.compliance import moderator_runs_locally

VERSION = "2026-10-draft-7"  # draft-7: studies done alone, in text, at any time have their own paragraph; draft-6: how things are said (pace, pauses, volume) and emoji are noted; draft-5: the AI also reads the screening answers; draft-4: AI-moderator disclosure (AI interviews only)

# A study done alone: replaces the picture of a live video call that the base text gives.
TEXT_ONLY_TEXTS = {
    "en": "This study is a written conversation that you complete on your own, at any time that suits you. No video or sound is recorded: what you type is the record. "
          "Nobody watches live; the research team reads the conversation afterwards. In what follows, \"recording\" means your typed answers, and \"observers\" means team members who read them later.",
    "zh": "这项研究是一场文字对话，由您自己在方便的时间完成。不录像、不录音，您输入的文字就是记录。没有人在现场旁观，研究团队会在事后阅读这场对话。"
          "下文中的“录制”指您输入的文字，“观察员”指事后阅读这些文字的团队成员。",
    "ms": "Kajian ini ialah perbualan bertulis yang anda selesaikan sendiri pada bila-bila masa yang sesuai. Tiada video atau bunyi dirakam: apa yang anda taip ialah rekod. "
          "Tiada sesiapa menonton secara langsung; pasukan penyelidik membaca perbualan itu kemudian. Di bawah, \"rakaman\" bermaksud jawapan yang anda taip, dan \"pemerhati\" bermaksud ahli pasukan yang membacanya kemudian.",
    "ta": "இந்த ஆய்வு நீங்களே, உங்களுக்கு வசதியான நேரத்தில் முடிக்கும் எழுத்து உரையாடல். வீடியோவோ ஒலியோ பதிவு செய்யப்படாது: நீங்கள் தட்டச்சு செய்வதே பதிவு. "
          "யாரும் நேரலையில் பார்ப்பதில்லை; ஆய்வுக் குழு பின்னர் உரையாடலைப் படிக்கும். கீழே “பதிவு” என்பது நீங்கள் தட்டச்சு செய்த பதில்கள், “பார்வையாளர்கள்” என்பது அவற்றைப் பின்னர் படிக்கும் குழு உறுப்பினர்கள்.",
}

# How things are said, and emoji: stated in every interview (the live captions are always on). Numbers and the participant's own emoji only.
SIGNALS_TEXTS = {
    "en": "To help the research team see where to ask more, we note how things are said (speaking pace, pauses and volume, compared only with your own usual way of speaking) "
          "and any emoji you choose to send. We do not judge your feelings from this: only these numbers and your own emoji are kept.",
    "zh": "为了帮助研究团队知道哪些地方值得多问，我们会记录您说话的语速、停顿和音量（只和您自己平时的说话习惯比较），以及您主动发送的表情。"
          "我们不会据此判断您的情绪，只保存这些数值和您自己发的表情。",
    "ms": "Untuk membantu pasukan penyelidik mengetahui di mana perlu bertanya lebih lanjut, kami mencatat cara anda bercakap (kelajuan, jeda dan kelantangan, dibandingkan dengan cara "
          "biasa anda sendiri bercakap) dan sebarang emoji yang anda pilih untuk hantar. Kami tidak menilai perasaan anda daripada ini: hanya nombor ini dan emoji anda sendiri disimpan.",
    "ta": "ஆய்வுக் குழு எங்கு மேலும் கேட்கலாம் என்பதை அறிய உதவ, நீங்கள் பேசும் விதத்தை (உங்கள் வழக்கமான பேச்சுடன் மட்டும் ஒப்பிட்ட வேகம், இடைநிறுத்தம், ஒலி அளவு) "
          "மற்றும் நீங்களாக அனுப்பும் எமோஜிகளை நாங்கள் பதிவு செய்கிறோம். இதிலிருந்து உங்கள் உணர்வுகளை நாங்கள் மதிப்பிடுவதில்லை: இந்த எண்களும் நீங்கள் அனுப்பிய எமோஜிகளும் மட்டுமே சேமிக்கப்படும்.",
}


TEXTS = {
    "en": (
        "This interview is run by TOTAL on behalf of {org}. We will record video and audio, transcribe it and translate it into Chinese "
        "for market research. We will not share your name or contact details with the client without your separate consent. "
        "Client observers may listen silently and you will not see them. Your information is stored on secure servers in Singapore and is accessed by our team in China, where we apply "
        "protections comparable to those required in Singapore. It is deleted 180 days after the research project ends. "
        "You may withdraw your consent, or ask to access, correct or delete your data, at any time by contacting {dpo}."
    ),
    "zh": (
        "本次访谈由星桐代 {org} 进行。我们会录制访谈的视频和声音，转写成文字并翻译成中文，用于市场研究。"
        "未经您另行同意，我们不会把您的姓名或联系方式提供给委托方。委托方的观察员可能静默旁听，您看不到他们。"
        "您的信息存放在位于新加坡的安全服务器上，并由我们在中国的团队访问，我们会妥善保管，并采取与新加坡要求相当的保护措施。研究项目结束 180 天后删除。"
        "您可以随时撤回同意，或要求查阅、更正、删除您的数据，联系 {dpo} 即可。"
    ),
    "ms": (
        "Temu bual ini dijalankan oleh TOTAL bagi pihak {org}. Kami akan merakam video dan audio, menyalinnya kepada teks dan menterjemahkannya "
        "ke bahasa Cina untuk kajian pasaran. Nama dan butiran hubungan anda tidak akan dikongsi dengan klien tanpa persetujuan berasingan anda. "
        "Pemerhati klien mungkin mendengar secara senyap dan anda tidak akan melihat mereka. Maklumat anda disimpan di pelayan selamat di Singapura dan diakses oleh pasukan kami di China; "
        "kami menjaganya dengan selamat dan menggunakan perlindungan yang setanding dengan yang diperlukan di Singapura. "
        "Maklumat itu dipadam 180 hari selepas projek kajian tamat. Anda boleh menarik balik persetujuan, atau meminta akses, pembetulan "
        "atau pemadaman data anda pada bila-bila masa dengan menghubungi {dpo}."
    ),
    "ta": (
        "இந்த நேர்காணலை {org} சார்பாக TOTAL நடத்துகிறது. சந்தை ஆய்வுக்காக உங்கள் காணொளி மற்றும் ஒலியை நாங்கள் பதிவு செய்து, "
        "எழுத்தாக மாற்றி, சீன மொழியில் மொழிபெயர்ப்போம். உங்கள் தனி ஒப்புதல் இல்லாமல் உங்கள் பெயர் அல்லது தொடர்பு விவரங்களை "
        "வாடிக்கையாளருடன் பகிர மாட்டோம். வாடிக்கையாளர் பார்வையாளர்கள் அமைதியாகக் கேட்கலாம்; நீங்கள் அவர்களைப் பார்க்க முடியாது. "
        "உங்கள் தகவல் சிங்கப்பூரில் உள்ள பாதுகாப்பான சேவையகங்களில் சேமிக்கப்பட்டு, சீனாவில் உள்ள எங்கள் குழுவால் அணுகப்படும்; அதைப் பாதுகாப்பாக வைத்து, சிங்கப்பூரில் தேவைப்படும் அளவுக்கு இணையான "
        "பாதுகாப்புகளைப் பயன்படுத்துவோம். ஆய்வுத் திட்டம் முடிந்த 180 நாட்களுக்குப் பிறகு அது நீக்கப்படும். எந்த நேரத்திலும் "
        "{dpo} ஐத் தொடர்புகொண்டு ஒப்புதலைத் திரும்பப் பெறலாம், அல்லது உங்கள் தரவைப் பார்க்க, திருத்த, நீக்கக் கோரலாம்."
    ),
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


# Only for interviews run by the AI moderator: it must be disclosed up front (red line H3). A human-moderated interview never shows it.
AI_TEXTS = {
    "en": "This interview is moderated by an AI interviewer, not a person: it asks the questions and follows up on your answers in a text chat. "
          "A member of our research team can see the conversation and may step in at any time. The AI will never ask for your name, "
          "phone number or ID. You can ask to stop at any time.",
    "zh": "本次访谈由 AI 主持人进行，不是真人：它会在文字聊天中提问，并就您的回答继续追问。我们研究团队的成员能看到对话，并可随时接手。"
          "AI 不会询问您的姓名、电话或证件。您可以随时要求停止。",
    "ms": "Temu bual ini dikendalikan oleh pewawancara AI, bukan manusia: ia bertanya soalan dan bertanya lanjut tentang jawapan anda dalam sembang teks. "
          "Ahli pasukan kajian kami boleh melihat perbualan dan boleh mengambil alih pada bila-bila masa. AI tidak akan meminta nama, "
          "nombor telefon atau dokumen pengenalan anda. Anda boleh meminta untuk berhenti pada bila-bila masa.",
    "ta": "இந்த நேர்காணலை ஒரு மனிதர் அல்ல, AI நேர்காணலாளர் நடத்துகிறது: அது உரை அரட்டையில் கேள்விகள் கேட்டு, உங்கள் பதில்களைப் பற்றி மேலும் கேட்கும். "
          "எங்கள் ஆய்வுக் குழு உறுப்பினர் ஒருவர் உரையாடலைப் பார்க்க முடியும், எந்த நேரத்திலும் பொறுப்பேற்கலாம். AI உங்கள் பெயர், தொலைபேசி எண் "
          "அல்லது அடையாள ஆவணத்தைக் கேட்காது. எந்த நேரத்திலும் நிறுத்தச் சொல்லலாம்.",
}
# Where the AI runs. A cloud model is one more transfer of the participant's words, so the provider is named (task doc part 3).
AI_LOCAL = {
    "en": "The AI runs on our own servers; your answers are not sent to any other company for this.",
    "zh": "AI 运行在我们自己的服务器上，您的回答不会为此发送给其他公司。",
    "ms": "AI berjalan pada pelayan kami sendiri; jawapan anda tidak dihantar kepada syarikat lain untuk tujuan ini.",
    "ta": "AI எங்கள் சொந்த சேவையகங்களில் இயங்குகிறது; இதற்காக உங்கள் பதில்கள் வேறு எந்த நிறுவனத்துக்கும் அனுப்பப்படாது.",
}
AI_CLOUD = {
    "en": "To run the AI, your answers are sent to {vendor}, an AI service provider, which processes them on our behalf for this interview only.",
    "zh": "为了运行 AI，您的回答会发送给 AI 服务提供商 {vendor}，由其仅为本次访谈代我们处理。",
    "ms": "Untuk menjalankan AI, jawapan anda dihantar kepada {vendor}, pembekal perkhidmatan AI, yang memprosesnya bagi pihak kami untuk temu bual ini sahaja.",
    "ta": "AI-ஐ இயக்க, உங்கள் பதில்கள் AI சேவை வழங்குநரான {vendor}-க்கு அனுப்பப்படும்; அது எங்கள் சார்பில் இந்த நேர்காணலுக்காக மட்டுமே அவற்றைச் செயலாக்கும்.",
}
# The screening questionnaire answers are background for the AI (it must not ask again what was already answered).
AI_PROFILE = {
    "en": "The AI can also see the screening questions you answered earlier, so that it does not ask the same things again.",
    "zh": "AI 也能看到您之前填写的甄别问卷答案，以免重复询问。",
    "ms": "AI juga boleh melihat soal selidik saringan yang anda jawab sebelum ini supaya tidak menanyakan perkara yang sama lagi.",
    "ta": "நீங்கள் முன்பு பதிலளித்த தேர்வு வினாக்களையும் AI பார்க்க முடியும்; அதனால் ஒரே கேள்விகளை மீண்டும் கேட்காது.",
}
AI_LABELS = {
    "en": "I understand the interviewer is an AI, not a person",
    "zh": "我知道本次访谈由 AI 主持，不是真人",
    "ms": "Saya faham pewawancara ialah AI, bukan manusia",
    "ta": "நேர்காணலாளர் மனிதர் அல்ல, AI என்பதை நான் புரிந்துகொள்கிறேன்",
}


def _ai_disclosure(lang: str) -> str:
    """The AI paragraph for one language: who moderates, and where the AI runs (a cloud provider is named)."""
    where = AI_LOCAL[lang] if moderator_runs_locally() else AI_CLOUD[lang].format(vendor=settings.moderator_vendor.strip())
    sep = "" if lang == "zh" else " "  # Chinese sentences are not separated by spaces
    return AI_TEXTS[lang] + sep + AI_PROFILE[lang] + sep + where + sep


def consent_for(lang: str, org_name: str, ai: bool = False, text_only: bool = False) -> dict:
    """The consent text a participant agrees to, and the checkbox labels.

    Args:
        lang: interview language; anything outside en/zh/ms/ta falls back to English.
        org_name: the client organisation named in the text.
        ai: True for an AI-moderated interview: the AI disclosure comes first and an extra "ai" checkbox is required.
        text_only: a study done alone, in writing, at any time (no video or sound; the team reads the conversation afterwards): says so first.

    Returns:
        {"version", "lang", "text", "labels", "ai", "sha256"}; sha256 is stored with the consent so the exact text can be proven later.
    """
    l = "zh" if (lang or "").startswith("zh") else lang if lang in TEXTS else "en"  # anything else: English
    text = TEXTS[l].format(org=org_name, dpo=settings.dpo_email)
    text = text + ("" if l == "zh" else " ") + SIGNALS_TEXTS[l]
    labels = dict(LABELS[l])
    if text_only:
        text = TEXT_ONLY_TEXTS[l] + (" " if l != "zh" else "") + text
    if ai:
        text = _ai_disclosure(l) + text
        labels = {"ai": AI_LABELS[l], **labels}
    return {"version": VERSION, "lang": l, "text": text, "labels": labels, "ai": ai, "sha256": hashlib.sha256(text.encode()).hexdigest()}
