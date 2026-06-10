import { useChatFiles } from "@plugins/chats/frontend/entities/chat";
import { Icon } from "@/shared/ui";

interface Props {
  chatId: string;
}

export function ChatsFiles({ chatId }: Props) {
  const { data: files = [] } = useChatFiles(chatId);
  return (
    <div className="files-view">
      {files.map((f, i) => (
        <div key={i} className="file-row">
          <div className="f-thumb">
            {f.kind === "pdf" ? <Icon.files /> : <Icon.attach />}
          </div>
          <div className="f-body">
            <div className="f-name">{f.name}</div>
            <div className="f-meta">
              {f.size} · {f.time}
            </div>
          </div>
          <button className="tb-btn">
            <Icon.download />
          </button>
        </div>
      ))}
    </div>
  );
}
